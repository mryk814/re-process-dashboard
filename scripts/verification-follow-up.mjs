import { appendFileSync, existsSync, readFileSync } from "node:fs";
import { resolve } from "node:path";

const reportPath = resolve(
  process.argv[2] ?? "artifacts/verification/latest-pr.json",
);
const summaryPath = process.env.GITHUB_STEP_SUMMARY;

function publish(lines) {
  const markdown = `${lines.join("\n")}\n`;
  process.stdout.write(markdown);
  if (summaryPath) appendFileSync(summaryPath, markdown);
}

if (!existsSync(reportPath)) {
  process.stdout.write(
    "::warning::direct verification report is unavailable; inspect the direct verification check\n",
  );
  publish([
    "## Verification follow-up",
    "- Direct verification report unavailable.",
    "- This notice does not replace or override the direct check.",
  ]);
  process.exit(0);
}

const report = JSON.parse(readFileSync(reportPath, "utf8"));
const followUps = report.required_follow_ups ?? [];
if (followUps.length === 0) {
  publish([
    "## Verification follow-up",
    "- No deferred checkpoint or release evidence.",
    `- Direct outcome: \`${report.outcome}\`.`,
  ]);
  process.exit(0);
}

for (const item of followUps) {
  process.stdout.write(
    `::warning title=Verification follow-up::${item.command} remains with ${item.owner}\n`,
  );
}
publish([
  "## Verification follow-up",
  `- Direct outcome: \`${report.outcome}\`.`,
  ...followUps.map(
    (item) => `- \`${item.command}\` — owner: **${item.owner}**; ${item.reason}`,
  ),
]);
