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
const ROW_HEIGHT = 72;
const GROUP_HEADER_HEIGHT = 34;
const GROUP_BOTTOM_PADDING = 8;

type TestGroup = {
  key: string;
  parent: GraphNode;
  entityType: string;
  nodes: GraphNode[];
};

type StageItem =
  | { kind: "node"; node: GraphNode }
  | { kind: "group"; group: TestGroup; expanded: boolean };

function testLabel(entityType: string): string {
  if (entityType === "熱延引張") return "熱延引張";
  if (entityType === "熱延組織") return "熱延組織";
  if (entityType === "焼鈍引張") return "引張";
  if (entityType === "焼鈍穴広げ") return "穴広げ";
  if (entityType === "焼鈍組織") return "組織";
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
    if (node?.entity_type === parentType) return node;
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
  const states = ["lineage-graph-node"];
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
  onLoadMore,
}: {
  graph: Graph;
  selectedKey: string;
  onSelect: (key: string) => void;
  onLoadMore: () => void;
}) {
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(new Set());
  const { upstream, downstream, incoming } = graphContext(graph, selectedKey);
  const nodesByKey = new Map(graph.nodes.map((node) => [node.key, node]));
  const grouped = STAGES.map((stage) => ({
    ...stage,
    nodes: graph.nodes
      .filter((node) => stage.types.includes(node.entity_type as never))
      .sort((left, right) => Number(right.key === selectedKey) - Number(left.key === selectedKey) || left.key.localeCompare(right.key)),
  }));
  const knownTypes = new Set(STAGES.flatMap((stage) => [...stage.types]));
  const unknownNodes = graph.nodes.filter((node) => !knownTypes.has(node.entity_type as never));
  if (unknownNodes.length) grouped[grouped.length - 1].nodes.push(...unknownNodes);

  const stageItems: Array<{ label: string; nodes: GraphNode[]; items: StageItem[] }> = grouped.map((stage) => {
    const items: StageItem[] = [];
    const groups = new Map<string, TestGroup>();
    stage.nodes.forEach((node) => {
      const parentType = parentTypeForTest(node.entity_type);
      const parent = parentType ? findProcessParent(node.key, parentType, incoming, nodesByKey) : null;
      if (!parent) {
        items.push({ kind: "node", node });
        return;
      }
      const key = `test-group:${parent.entity_type}:${parent.key}:${node.entity_type}`;
      const group = groups.get(key) ?? { key, parent, entityType: node.entity_type, nodes: [] };
      group.nodes.push(node);
      groups.set(key, group);
    });
    if (groups.size) {
      const seen = new Set<string>();
      const ordered: StageItem[] = [];
      stage.nodes.forEach((node) => {
        const parentType = parentTypeForTest(node.entity_type);
        const parent = parentType ? findProcessParent(node.key, parentType, incoming, nodesByKey) : null;
        const key = parent ? `test-group:${parent.entity_type}:${parent.key}:${node.entity_type}` : null;
        if (!key) {
          ordered.push({ kind: "node", node });
        } else if (!seen.has(key)) {
          seen.add(key);
          ordered.push({ kind: "group", group: groups.get(key)!, expanded: !collapsedGroups.has(key) });
        }
      });
      return { ...stage, items: ordered };
    }
    return { ...stage, items };
  });

  const positions = new Map<string, { x: number; y: number }>();
  const groupLayouts: Array<TestGroup & { x: number; y: number; height: number; expanded: boolean }> = [];
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
      cursor += height;
    });
    return cursor;
  });
  const width = 24 + STAGES.length * STAGE_WIDTH;
  const height = Math.max(300, ...stageHeights.map((stageHeight) => stageHeight + 20));

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

  return (
    <section className="lineage-graph-panel" aria-label={`${selectedKey} の実工程系譜`}>
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
              className={`lineage-graph-group ${group.expanded ? "expanded" : "collapsed"}`}
              key={group.key}
              style={{ left: group.x - 8, top: group.y, width: NODE_WIDTH + 16, height: group.height }}
            >
              <button
                type="button"
                className="lineage-graph-group-toggle"
                aria-expanded={group.expanded}
                aria-label={`${group.parent.entity_type} ${group.parent.key} の${testLabel(group.entityType)}を${group.expanded ? "折りたたむ" : "展開する"}`}
                title={`${group.parent.entity_type} ${group.parent.key} から伸びる${testLabel(group.entityType)}`}
                onClick={() => setCollapsedGroups((current) => {
                  const next = new Set(current);
                  if (next.has(group.key)) next.delete(group.key);
                  else next.add(group.key);
                  return next;
                })}
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
            {graph.edges.map((edge) => {
              const source = positions.get(edge.source);
              const target = positions.get(edge.target);
              if (!source || !target) return null;
              const sourceUpstream = upstream.has(edge.source);
              const targetUpstream = upstream.has(edge.target) || edge.target === selectedKey;
              const sourceDownstream = downstream.has(edge.source) || edge.source === selectedKey;
              const targetDownstream = downstream.has(edge.target);
              const state = sourceUpstream && targetUpstream
                ? "upstream"
                : sourceDownstream && targetDownstream
                  ? "downstream"
                  : "context";
              const x1 = source.x + NODE_WIDTH;
              const y1 = source.y + NODE_HEIGHT / 2;
              const x2 = target.x;
              const y2 = target.y + NODE_HEIGHT / 2;
              const bend = Math.max(28, (x2 - x1) * 0.45);
              return (
                <path
                  key={`${edge.source}-${edge.target}`}
                  className={`lineage-graph-edge ${state}`}
                  d={`M ${x1} ${y1} C ${x1 + bend} ${y1}, ${x2 - bend} ${y2}, ${x2} ${y2}`}
                  markerEnd="url(#lineage-arrow)"
                >
                  <title>{`${edge.source} → ${edge.target} / relation ${edge.route_rows.join(", ")}`}</title>
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
