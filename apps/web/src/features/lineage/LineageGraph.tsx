import { useState } from "react";

import type { ApiLineage } from "../../shared/api/workbench-api";

type Graph = ApiLineage["graph"];
type GraphNode = Graph["nodes"][number];

const STAGES = [
  { label: "材料", types: ["溶製"] },
  { label: "熱延", types: ["熱延"] },
  { label: "熱延用の試験・組織", types: ["熱延引張", "熱延組織"] },
  { label: "冷延", types: ["冷延"] },
  { label: "焼鈍", types: ["焼鈍"] },
  { label: "試験・組織", types: ["焼鈍引張", "焼鈍穴広げ", "焼鈍組織"] },
] as const;

const ISSUE_LABELS: Record<string, string> = {
  missing_key: "キー欠損",
  orphan_entity: "孤立",
  duplicate_key: "重複",
  invalid_reference: "参照切れ",
};

const NODE_WIDTH = 120;
const NODE_HEIGHT = 54;
const STAGE_WIDTH = 160;
const TOP = 55;
const ROW_HEIGHT = 76;
const GROUP_HEADER_HEIGHT = 34;
const GROUP_BOTTOM_PADDING = 8;
const GROUP_GAP = 12;
const PROCESS_NODE_TYPES = new Set(["溶製", "熱延", "冷延", "焼鈍"]);

type TestGroup = {
  key: string;
  parent: GraphNode;
  entityType: string;
  nodes: GraphNode[];
};

type StageItem =
  | { kind: "node"; node: GraphNode }
  | { kind: "group"; group: TestGroup; expanded: boolean };

type GroupLayout = TestGroup & { x: number; y: number; height: number; expanded: boolean };
type RenderableEdge = {
  sourceKey: string;
  targetKey: string;
  routeRows: number[];
  state: "upstream" | "downstream" | "context";
};

function testLabel(entityType: string): string {
  if (entityType === "熱延引張") return "熱延引張";
  if (entityType === "熱延組織") return "熱延組織";
  if (entityType === "焼鈍引張") return "引張";
  if (entityType === "焼鈍穴広げ") return "穴広げ";
  if (entityType === "焼鈍組織") return "組織";
  return entityType;
}

function canonicalEntityType(entityType: string): string {
  const type = entityType.replace(/_key\*\*$/, "");
  if (type.startsWith("溶製")) return "溶製";
  if (type.startsWith("熱延")) {
    if (type.includes("引張")) return "熱延引張";
    if (type.includes("組織")) return "熱延組織";
    return "熱延";
  }
  if (type.startsWith("冷延")) return "冷延";
  if (type.startsWith("焼鈍")) {
    if (type.includes("引張")) return "焼鈍引張";
    if (type.includes("穴広げ") || type.includes("穴拡げ")) return "焼鈍穴広げ";
    if (type.includes("組織")) return "焼鈍組織";
    return "焼鈍";
  }
  return entityType;
}

function groupSummary(nodes: GraphNode[]): string {
  return nodes.map((node) => node.key).join(" · ");
}

function parentTypeForTest(entityType: string): "熱延" | "焼鈍" | null {
  if (entityType === "熱延引張" || entityType === "熱延組織") return "熱延";
  if (entityType === "焼鈍引張" || entityType === "焼鈍穴広げ" || entityType === "焼鈍組織") return "焼鈍";
  return null;
}

function groupColorClass(entityType: string): string {
  const classes: Record<string, string> = {
    熱延引張: "group-hot-tensile",
    熱延組織: "group-hot-microstructure",
    焼鈍引張: "group-annealed-tensile",
    焼鈍穴広げ: "group-annealed-hole-expansion",
    焼鈍組織: "group-annealed-microstructure",
  };
  return classes[entityType] ?? "";
}

function findProcessParent(
  startKey: string,
  parentType: "熱延" | "焼鈍",
  incoming: Map<string, string[]>,
  nodesByKey: Map<string, GraphNode>,
): GraphNode | null {
  const pending = [...(incoming.get(startKey) ?? [])];
  const visited = new Set<string>();
  while (pending.length) {
    const key = pending.shift();
    if (!key || visited.has(key)) continue;
    visited.add(key);
    const node = nodesByKey.get(key);
    if (node && canonicalEntityType(node.entity_type) === parentType) return node;
    pending.push(...(incoming.get(key) ?? []));
  }
  return null;
}

function reachable(start: string, adjacency: Map<string, string[]>): Set<string> {
  const visited = new Set<string>();
  const pending = [...(adjacency.get(start) ?? [])];
  while (pending.length) {
    const key = pending.pop();
    if (!key || visited.has(key)) continue;
    visited.add(key);
    pending.push(...(adjacency.get(key) ?? []));
  }
  return visited;
}

function graphContext(graph: Graph, selectedKey: string) {
  const outgoing = new Map<string, string[]>();
  const incoming = new Map<string, string[]>();
  graph.edges.forEach((edge) => {
    outgoing.set(edge.source, [...(outgoing.get(edge.source) ?? []), edge.target]);
    incoming.set(edge.target, [...(incoming.get(edge.target) ?? []), edge.source]);
  });
  return {
    upstream: reachable(selectedKey, incoming),
    downstream: reachable(selectedKey, outgoing),
    incoming,
    outgoing,
  };
}

function nodeState(node: GraphNode, selectedKey: string, upstream: Set<string>, downstream: Set<string>) {
  const colorClass = groupColorClass(canonicalEntityType(node.entity_type));
  const states = ["lineage-graph-node", colorClass].filter(Boolean);
  if (node.key === selectedKey) states.push("selected");
  else if (upstream.has(node.key)) states.push("upstream");
  else if (downstream.has(node.key)) states.push("downstream");
  else states.push("context");
  if (!node.exists) states.push("missing");
  if (node.issue_types.includes("orphan_entity")) states.push("orphan");
  if (node.issue_types.includes("duplicate_key")) states.push("duplicate");
  if (node.issue_types.includes("invalid_reference")) states.push("invalid-reference");
  return states.join(" ");
}

export function LineageGraph({
  graph,
  selectedKey,
  onSelect,
  onGroupSelect,
  onLoadMore,
}: {
  graph: Graph;
  selectedKey: string;
  onSelect: (key: string) => void;
  onGroupSelect?: (selection: { parentKey: string; entityType: string; nodeKeys: string[] }) => void;
  onLoadMore: () => void;
}) {
  const [expandedGroups, setExpandedGroups] = useState<Set<string>>(new Set());
  const { upstream, downstream, incoming } = graphContext(graph, selectedKey);
  const nodesByKey = new Map(graph.nodes.map((node) => [node.key, node]));
  const grouped = STAGES.map((stage) => ({
    ...stage,
    nodes: graph.nodes
      .filter((node) => stage.types.includes(canonicalEntityType(node.entity_type) as never))
      .sort((left, right) => Number(right.key === selectedKey) - Number(left.key === selectedKey) || left.key.localeCompare(right.key)),
  }));
  const knownTypes = new Set(STAGES.flatMap((stage) => [...stage.types]));
  const unknownNodes = graph.nodes.filter((node) => !knownTypes.has(canonicalEntityType(node.entity_type) as never));
  if (unknownNodes.length) grouped[grouped.length - 1].nodes.push(...unknownNodes);

  const stageItems: Array<{ label: string; nodes: GraphNode[]; items: StageItem[] }> = grouped.map((stage) => {
    const items: StageItem[] = [];
    const groups = new Map<string, TestGroup>();
    stage.nodes.forEach((node) => {
      const normalizedType = canonicalEntityType(node.entity_type);
      const parentType = parentTypeForTest(normalizedType);
      const parent = parentType ? findProcessParent(node.key, parentType, incoming, nodesByKey) : null;
      if (!parent) {
        items.push({ kind: "node", node });
        return;
      }
      const key = `test-group:${parent.entity_type}:${parent.key}:${normalizedType}`;
      const group = groups.get(key) ?? { key, parent, entityType: normalizedType, nodes: [] };
      group.nodes.push(node);
      groups.set(key, group);
    });
    if (groups.size) {
      const typeOrder = new Map<string, number>(stage.types.map((type, index) => [type, index] as [string, number]));
      const orderedGroups = [...groups.values()].sort((left, right) => (
        left.parent.key.localeCompare(right.parent.key, undefined, { numeric: true })
        || (typeOrder.get(left.entityType) ?? Number.MAX_SAFE_INTEGER) - (typeOrder.get(right.entityType) ?? Number.MAX_SAFE_INTEGER)
        || left.key.localeCompare(right.key)
      ));
      const groupedNodeKeys = new Set(orderedGroups.flatMap((group) => group.nodes.map((node) => node.key)));
      const ordered: StageItem[] = orderedGroups.map((group) => ({
        kind: "group",
        group,
        expanded: expandedGroups.has(group.key) || group.nodes.some((groupNode) => groupNode.key === selectedKey),
      }));
      ordered.push(...stage.nodes.filter((node) => !groupedNodeKeys.has(node.key)).map((node) => ({ kind: "node" as const, node })));
      return { ...stage, items: ordered };
    }
    return { ...stage, items };
  });

  const positions = new Map<string, { x: number; y: number }>();
  const groupLayouts: GroupLayout[] = [];
  const stageHeights = stageItems.map((stage, stageIndex) => {
    const x = 24 + stageIndex * STAGE_WIDTH;
    let cursor = TOP;
    stage.items.forEach((item) => {
      if (item.kind === "node") {
        positions.set(item.node.key, { x, y: cursor });
        cursor += ROW_HEIGHT;
        return;
      }
      const height = GROUP_HEADER_HEIGHT + (item.expanded ? item.group.nodes.length * ROW_HEIGHT : 0) + GROUP_BOTTOM_PADDING;
      const anchor = { x, y: cursor + GROUP_HEADER_HEIGHT / 2 - NODE_HEIGHT / 2 };
      item.group.nodes.forEach((node, index) => {
        positions.set(node.key, item.expanded ? { x, y: cursor + GROUP_HEADER_HEIGHT + index * ROW_HEIGHT } : anchor);
      });
      groupLayouts.push({ ...item.group, x, y: cursor, height, expanded: item.expanded });
      cursor += height + GROUP_GAP;
    });
    return cursor;
  });
  const width = 24 + STAGES.length * STAGE_WIDTH;
  const height = Math.max(300, ...stageHeights.map((stageHeight) => stageHeight + 20));
  const groupByNodeKey = new Map<string, GroupLayout>();
  groupLayouts.forEach((group) => group.nodes.forEach((node) => groupByNodeKey.set(node.key, group)));
  const edgeStateRank = { context: 0, upstream: 1, downstream: 2 } as const;
  const renderableEdges = new Map<string, RenderableEdge>();
  graph.edges.forEach((edge) => {
    const sourceGroup = groupByNodeKey.get(edge.source);
    const targetGroup = groupByNodeKey.get(edge.target);
    const sourceKey = sourceGroup && !sourceGroup.expanded ? sourceGroup.key : edge.source;
    const targetKey = targetGroup && !targetGroup.expanded ? targetGroup.key : edge.target;
    if (sourceKey === targetKey) return;
    const sourceUpstream = upstream.has(edge.source);
    const targetUpstream = upstream.has(edge.target) || edge.target === selectedKey;
    const sourceDownstream = downstream.has(edge.source) || edge.source === selectedKey;
    const targetDownstream = downstream.has(edge.target);
    const state = sourceUpstream && targetUpstream
      ? "upstream"
      : sourceDownstream && targetDownstream
        ? "downstream"
        : "context";
    const bucketKey = `${sourceKey}\u001f${targetKey}`;
    const existing = renderableEdges.get(bucketKey);
    if (existing) {
      existing.routeRows = [...new Set([...existing.routeRows, ...edge.route_rows])];
      if (edgeStateRank[state] > edgeStateRank[existing.state]) existing.state = state;
    } else {
      renderableEdges.set(bucketKey, { sourceKey, targetKey, routeRows: [...edge.route_rows], state });
    }
  });
  const endpointPosition = (key: string) => {
    const group = groupLayouts.find((candidate) => candidate.key === key);
    return group && !group.expanded
      ? { x: group.x, y: group.y + GROUP_HEADER_HEIGHT / 2 - NODE_HEIGHT / 2 }
      : positions.get(key);
  };
  const endpointEntityType = (key: string) => {
    const group = groupLayouts.find((candidate) => candidate.key === key);
    return group && !group.expanded ? group.entityType : nodesByKey.get(key) ? canonicalEntityType(nodesByKey.get(key)!.entity_type) : undefined;
  };
  const endpointLabel = (key: string) => {
    const group = groupLayouts.find((candidate) => candidate.key === key);
    return group && !group.expanded ? `${group.parent.key} ${testLabel(group.entityType)}` : key;
  };

  const renderNode = (node: GraphNode) => {
    const position = positions.get(node.key);
    if (!position) return null;
    const issues = node.issue_types.map((issue) => ISSUE_LABELS[issue] ?? issue);
    const stateLabels = [...(!node.exists ? ["欠損先"] : []), ...issues];
    return (
      <button
        type="button"
        key={node.key}
        className={nodeState(node, selectedKey, upstream, downstream)}
        style={{ left: position.x, top: position.y, width: NODE_WIDTH, height: NODE_HEIGHT }}
        onClick={() => onSelect(node.key)}
        aria-current={node.key === selectedKey ? "true" : undefined}
        title={`${node.entity_type}${issues.length ? ` / ${issues.join(" / ")}` : ""}`}
      >
        <b>{node.key}</b>
        <span>{node.entity_type}</span>
        {stateLabels.length > 0 && <em>{stateLabels.join(" / ")}</em>}
      </button>
    );
  };

  const processEdgePath = (x1: number, y1: number, x2: number, y2: number, lane = 0) => {
    const routeY = TOP - 16 - lane * 9;
    const bend = Math.max(24, (x2 - x1) * 0.18);
    return `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x1 + bend} ${routeY}, ${x1 + bend * 2} ${routeY} L ${x2 - bend * 2} ${routeY} C ${x2 - bend} ${routeY}, ${x2 - bend} ${y2}, ${x2} ${y2}`;
  };

  const edgeKey = (edge: RenderableEdge) => `${edge.sourceKey}-${edge.targetKey}`;
  const isRoutedProcessEdge = (edge: RenderableEdge) => {
    const source = endpointPosition(edge.sourceKey);
    const target = endpointPosition(edge.targetKey);
    const sourceEntityType = endpointEntityType(edge.sourceKey);
    const targetEntityType = endpointEntityType(edge.targetKey);
    return Boolean(
      source && target && sourceEntityType && targetEntityType
      && PROCESS_NODE_TYPES.has(sourceEntityType)
      && PROCESS_NODE_TYPES.has(targetEntityType)
      && target.x - source.x > STAGE_WIDTH + 20,
    );
  };
  const processRouteLanes = new Map<string, number>();
  const processRouteLaneCounts = new Map<string, number>();
  renderableEdges.forEach((edge) => {
    if (!isRoutedProcessEdge(edge)) return;
    const source = endpointPosition(edge.sourceKey);
    const target = endpointPosition(edge.targetKey);
    if (!source || !target) return;
    const corridor = `${source.x}:${target.x}`;
    const lane = processRouteLaneCounts.get(corridor) ?? 0;
    processRouteLanes.set(edgeKey(edge), lane);
    processRouteLaneCounts.set(corridor, lane + 1);
  });

  return (
    <section className="lineage-graph-panel" aria-label={`${selectedKey} の工程・試験関係`}>
      <header className="lineage-graph-header">
        <div>
          <b>{graph.relation_row_count} relation行から復元</b>
          <span>{graph.visible_node_count}/{graph.total_node_count}ノード表示 · 初期上限40件</span>
        </div>
        <div className="lineage-graph-legend" aria-label="グラフ凡例">
          <span className="upstream">上流</span>
          <span className="downstream">下流</span>
          <span className="missing">欠損先</span>
          <span className="issue">品質問題</span>
        </div>
      </header>
      <div className="lineage-graph-scroll">
        <div className="lineage-graph-surface" style={{ width, height }} data-testid="lineage-real-graph">
          {stageItems.map((stage, index) => (
            <div className="lineage-graph-stage" key={stage.label} style={{ left: 24 + index * STAGE_WIDTH }}>
              <b>{stage.label}</b><small>{stage.nodes.length}</small>
            </div>
          ))}
          {groupLayouts.map((group) => (
            <div
              className={`lineage-graph-group ${groupColorClass(group.entityType)} ${group.expanded ? "expanded" : "collapsed"}`}
              key={group.key}
              style={{ left: group.x - 8, top: group.y, width: NODE_WIDTH + 16, height: group.height }}
            >
              <button
                type="button"
                className="lineage-graph-group-toggle"
                aria-expanded={group.expanded}
                aria-label={`${group.parent.entity_type} ${group.parent.key} の${testLabel(group.entityType)}を${group.expanded ? "折りたたむ" : "展開する"}`}
                title={`${group.parent.entity_type} ${group.parent.key} から伸びる${testLabel(group.entityType)}`}
                onClick={() => {
                  onSelect(group.parent.key);
                  onGroupSelect?.({
                    parentKey: group.parent.key,
                    entityType: group.entityType,
                    nodeKeys: group.nodes.map((node) => node.key),
                  });
                  setExpandedGroups((current) => {
                    const next = new Set(current);
                    if (next.has(group.key)) next.delete(group.key);
                    else next.add(group.key);
                    return next;
                  });
                }}
              >
                <b>{group.parent.key}</b>
                <span>{testLabel(group.entityType)} {group.nodes.length}件</span>
                <small>{groupSummary(group.nodes)}</small>
                <em aria-hidden="true">{group.expanded ? "−" : "+"}</em>
              </button>
            </div>
          ))}
          <svg className="lineage-graph-edges" width={width} height={height} aria-hidden="true">
            <defs>
              <marker id="lineage-arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
                <path d="M 0 0 L 10 5 L 0 10 z" />
              </marker>
            </defs>
            {[...renderableEdges.values()].map((edge) => {
              const source = endpointPosition(edge.sourceKey);
              const target = endpointPosition(edge.targetKey);
              if (!source || !target) return null;
              const x1 = source.x + NODE_WIDTH;
              const y1 = source.y + NODE_HEIGHT / 2;
              const x2 = target.x;
              const y2 = target.y + NODE_HEIGHT / 2;
              const bend = Math.max(28, (x2 - x1) * 0.45);
              const routedProcessEdge = processRouteLanes.has(edgeKey(edge));
              const routeLane = processRouteLanes.get(edgeKey(edge)) ?? 0;
              return (
                <path
                  key={`${edge.sourceKey}-${edge.targetKey}`}
                  className={`lineage-graph-edge ${edge.state}${routedProcessEdge ? " process-route" : ""}`}
                  d={routedProcessEdge ? processEdgePath(x1, y1, x2, y2, routeLane) : `M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`}
                  markerEnd="url(#lineage-arrow)"
                >
                  <title>{`${endpointLabel(edge.sourceKey)} → ${endpointLabel(edge.targetKey)} / relation ${edge.routeRows.join(", ")}`}</title>
                </path>
              );
            })}
          </svg>
          {stageItems.flatMap((stage) => stage.items).flatMap((item) => item.kind === "node" ? [renderNode(item.node)] : item.expanded ? item.group.nodes.map(renderNode) : [])}
        </div>
      </div>
      {graph.has_more && (
        <button type="button" className="secondary-button lineage-load-more" onClick={onLoadMore}>
          さらに40件読み込む（残り{graph.omitted_node_count}）
        </button>
      )}
    </section>
  );
}
