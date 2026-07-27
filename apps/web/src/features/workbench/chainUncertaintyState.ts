/**
 * Chainの点推定セルに出す不確かさの状態。
 *
 * 点推定だけのChain実行では、どの行にも区間が付かない。「区間なし」とだけ書くと
 * 「この予測に不確かさが無い」と読めるが、実際は別操作の伝播Runで得られる。
 * 未計算・非対応・Run対象外を分けて言う。
 */
export type ChainUncertaintyAvailability = {
  /** Stage IDごとの伝播対応可否。取得前やreadOnlyでは undefined。 */
  supportedStages?: Record<string, boolean>;
  /** 現revisionに対する伝播Runが保存済みか。 */
  runComputed: boolean;
};

export type ChainUncertaintyStatus =
  | "interval"
  | "not_computed"
  | "point_only"
  | "outside_run";

export function chainUncertaintyStatus(
  hasInterval: boolean,
  availability: ChainUncertaintyAvailability,
  stageId?: string,
): ChainUncertaintyStatus {
  if (hasInterval) return "interval";
  const supported = stageId === undefined
    ? undefined
    : availability.supportedStages?.[stageId];
  if (supported === false) return "point_only";
  if (availability.runComputed) return "outside_run";
  return "not_computed";
}

export function chainUncertaintyLabel(status: ChainUncertaintyStatus): string {
  switch (status) {
    case "point_only":
      return "区間なし（このStageは点推定のみ）";
    case "outside_run":
      return "この出力は伝播Runの対象外";
    case "not_computed":
      return "不確かさ未計算";
    default:
      return "";
  }
}

export function chainUncertaintyStageNote(
  availability: ChainUncertaintyAvailability,
  stageId: string,
): string {
  if (availability.supportedStages?.[stageId] === false) {
    return "このStageは点推定のみです。伝播Runを実行しても区間は出ません。";
  }
  if (availability.runComputed) {
    return "このStageの不確かさは計算済みです。区間は「不確かさを伝播」で確認できます。";
  }
  return "このStageの不確かさは未計算です。伝播Runを実行すると5–95%区間を計算します。";
}
