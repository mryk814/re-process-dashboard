import { useEffect, useState, type ReactNode } from "react";
import { isTargetRange, type TargetGoal } from "../../shared/targetGoals";
import type {
  ApiProject,
  ApiProjectSeries,
  ApiTaskDefinition,
} from "../../shared/api/workbench-api";
import {
  projectSettingsControlsDisabled,
  projectSettingsSaveDisabled,
  shouldShowProjectSettings,
  ungroupedMembershipValue,
} from "./projectSettingsState";

export const defaultGoalLabel = (
  direction: "at_least" | "at_most" | "target",
) => direction === "at_most"
  ? "以下"
  : direction === "target"
    ? "目標値付近"
    : "以上";

type OutputDefinition = ApiTaskDefinition["task_definition"]["outputs"][number];
type ProjectSeriesOption = Pick<ApiProjectSeries, "id" | "name">;

export type ProjectSettingsPanelProps = {
  open: boolean;
  project: ApiProject | null;
  loading: boolean;
  projectError: string;
  groupNameError: string;
  groupMembershipError: string;
  disabled: boolean;
  outputs: OutputDefinition[];
  targetValues: Record<string, TargetGoal>;
  invalidTargetRange: boolean;
  showActiveSeriesMembership: boolean;
  groupSettingsOpen: boolean;
  fixedSeries?: ProjectSeriesOption;
  activeProjectSeries: ProjectSeriesOption[];
  groupMembershipId: string;
  membershipChanged: boolean;
  membershipTargetSeriesId: string | null;
  membershipEmptiesFixedSeries: boolean;
  seriesName: string;
  scientificSettings?: ReactNode;
  onOpenGroupSettings: () => void;
  onGroupMembershipChange: (seriesId: string) => void;
  onMoveProjectToGroup: () => void | Promise<void>;
  onSeriesNameChange: (name: string) => void;
  onSaveSeriesName: () => void | Promise<void>;
  onProjectChange: (project: ApiProject) => void;
  onTargetModeChange: (
    outputKey: string,
    mode: "directional" | "between",
  ) => void;
  onScalarTargetChange: (outputKey: string, value: string) => void;
  onRangeTargetChange: (
    outputKey: string,
    bound: "lower" | "upper",
    value: string,
  ) => void;
  onSave: () => void | Promise<void>;
};

export function ProjectSettingsPanel({
  open,
  project,
  loading,
  projectError,
  groupNameError,
  groupMembershipError,
  disabled,
  outputs,
  targetValues,
  invalidTargetRange,
  showActiveSeriesMembership,
  groupSettingsOpen,
  fixedSeries,
  activeProjectSeries,
  groupMembershipId,
  membershipChanged,
  membershipTargetSeriesId,
  membershipEmptiesFixedSeries,
  seriesName,
  scientificSettings,
  onOpenGroupSettings,
  onGroupMembershipChange,
  onMoveProjectToGroup,
  onSeriesNameChange,
  onSaveSeriesName,
  onProjectChange,
  onTargetModeChange,
  onScalarTargetChange,
  onRangeTargetChange,
  onSave,
}: ProjectSettingsPanelProps) {
  const [memoOpen, setMemoOpen] = useState(Boolean(project?.notes));
  useEffect(() => {
    setMemoOpen(Boolean(project?.notes));
  }, [project?.id]);
  if (!shouldShowProjectSettings({ open, hasProject: Boolean(project) })) {
    return null;
  }
  if (!project) return null;

  const controlsDisabled = projectSettingsControlsDisabled({
    loading,
    disabled,
  });
  const saveDisabled = projectSettingsSaveDisabled({
    loading,
    disabled,
    projectName: project.name,
    invalidTargetRange,
  });

  return <section
    className="project-settings-panel"
    aria-label="プロジェクト設定"
    aria-busy={loading}
  >
    {projectError && <p className="panel-error" role="alert">{projectError}</p>}
    <div className="project-form">
      {!showActiveSeriesMembership && !groupSettingsOpen && <div className="project-group-entry">
        <button
          type="button"
          className="outline-button"
          disabled={controlsDisabled}
          onClick={onOpenGroupSettings}
        >ほかの検討とまとめる</button>
        <small>同じ目的で続けた複数の検討をまとめます。続き元の関係とは別です。</small>
      </div>}
      {(showActiveSeriesMembership || groupSettingsOpen) && <>
        <div className="group-membership-setting">
          <label>所属グループ
            <select
              value={groupMembershipId}
              disabled={controlsDisabled}
              onChange={(event) => onGroupMembershipChange(event.target.value)}
            >
              <option value="">選択してください</option>
              {project.project_series_id && <option value={ungroupedMembershipValue}>グループなし</option>}
              {activeProjectSeries.map((series) => <option key={series.id} value={series.id}>{series.name}</option>)}
            </select>
          </label>
          <button
            type="button"
            className="outline-button"
            disabled={controlsDisabled || !membershipChanged}
            onClick={() => void onMoveProjectToGroup()}
          >{membershipTargetSeriesId === null ? "このプロジェクトをグループから外す" : "このプロジェクトを移動"}</button>
          {groupMembershipError && (
            <small className="panel-error" role="alert">{groupMembershipError}</small>
          )}
          <small>同じ目的で続けた複数の検討をまとめます。続き元の関係とは別です。候補・判断履歴・続き元は変わりません。</small>
          {membershipEmptiesFixedSeries && <small className="warning-note">外すとグループ「{fixedSeries?.name}」は所属プロジェクトが無くなり、一覧から閉じられます。グループ名を選び直せば戻せます。</small>}
        </div>
        {fixedSeries && <div className="series-name-setting">
          <label>グループ名
            <input
              value={seriesName}
              disabled={controlsDisabled}
              onChange={(event) => onSeriesNameChange(event.target.value)}
            />
          </label>
          <button
            type="button"
            className="outline-button"
            disabled={controlsDisabled || !seriesName.trim()}
            onClick={() => void onSaveSeriesName()}
          >名前を保存</button>
          {groupNameError && <small className="panel-error" role="alert">{groupNameError}</small>}
          <small>このグループに含まれるすべてのプロジェクトへ反映されます</small>
        </div>}
      </>}
      {outputs.length > 0 && <fieldset
        className="target-grid"
        id="project-target-settings"
        disabled={controlsDisabled}
      >
        <legend>目標値</legend>
        {outputs.map((output) => {
          const goal = targetValues[output.key];
          const range = isTargetRange(goal) ? goal : undefined;
          const rangeOnly = output.goal_direction === "target";
          const showRange = rangeOnly || range != null;
          const rangeDraft = range ?? {
            lower: Number.NaN,
            upper: Number.NaN,
          };
          return <div className="target-setting" key={output.key}>
            <label>{output.label}
              <select
                disabled={controlsDisabled || rangeOnly}
                value={showRange ? "between" : "directional"}
                onChange={(event) => onTargetModeChange(
                  output.key,
                  event.target.value as "directional" | "between",
                )}
              >
                <option value="directional">{defaultGoalLabel(output.goal_direction)}</option>
                <option value="between">範囲内</option>
              </select>
            </label>
            {showRange
              ? <div className="target-range-inputs">
                <label>下限
                  <input
                    aria-label={`${output.label}の下限`}
                    type="number"
                    value={Number.isFinite(rangeDraft.lower) ? rangeDraft.lower : ""}
                    placeholder="下限"
                    onChange={(event) => onRangeTargetChange(
                      output.key,
                      "lower",
                      event.target.value,
                    )}
                  />
                </label>
                <span>–</span>
                <label>上限
                  <input
                    aria-label={`${output.label}の上限`}
                    type="number"
                    value={Number.isFinite(rangeDraft.upper) ? rangeDraft.upper : ""}
                    placeholder="上限"
                    onChange={(event) => onRangeTargetChange(
                      output.key,
                      "upper",
                      event.target.value,
                    )}
                  />
                </label>
              </div>
              : <input
                aria-label={`${output.label}の目標値`}
                type="number"
                value={typeof goal === "number" ? goal : ""}
                placeholder="未設定"
                onChange={(event) => onScalarTargetChange(
                  output.key,
                  event.target.value,
                )}
              />}
            {output.unit && <small className="target-setting-unit">{output.unit}</small>}
          </div>;
        })}
        {invalidTargetRange && <small className="target-range-error">範囲目標は、下限を上限より小さく設定してください。</small>}
      </fieldset>}
      {(project.description || project.purpose) && <details className="project-legacy-information">
        <summary>追加情報</summary>
        <dl>
          {project.purpose && <div><dt>以前の目的</dt><dd>{project.purpose}</dd></div>}
          {project.description && <div><dt>以前の説明</dt><dd>{project.description}</dd></div>}
        </dl>
        <small>既存値は保持しています。新しい補足はProjectメモへまとめます。</small>
      </details>}
      {!memoOpen && !project.notes
        ? <button type="button" className="outline-button project-memo-add" disabled={controlsDisabled} onClick={() => setMemoOpen(true)}>メモを追加</button>
        : <label>Projectメモ
          <textarea
            value={project.notes}
            disabled={controlsDisabled}
            onChange={(event) => onProjectChange({
              ...project,
              notes: event.target.value,
            })}
          />
        </label>}
    </div>
    <button
      type="button"
      className="primary-button"
      disabled={saveDisabled}
      onClick={() => void onSave()}
    >{loading ? "保存中…" : "設定を保存"}</button>
    {scientificSettings}
  </section>;
}
