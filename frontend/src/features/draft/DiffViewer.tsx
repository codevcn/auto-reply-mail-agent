import React, { useMemo } from "react";

export interface DiffLine {
  type: "added" | "removed" | "unchanged";
  text: string;
  origIndex?: number;
  modIndex?: number;
}

export interface DiffViewerProps {
  originalText: string;
  modifiedText: string;
  originalLabel?: string;
  modifiedLabel?: string;
}

/**
 * Computes simple line-by-line LCS difference between two text blocks.
 */
function computeLineDiff(original: string, modified: string): DiffLine[] {
  const origLines = original ? original.split(/\r?\n/) : [];
  const modLines = modified ? modified.split(/\r?\n/) : [];

  const m = origLines.length;
  const n = modLines.length;

  // LCS DP table
  const dp: number[][] = Array.from({ length: m + 1 }, () => Array(n + 1).fill(0));

  for (let i = 0; i < m; i++) {
    for (let j = 0; j < n; j++) {
      if (origLines[i] === modLines[j]) {
        dp[i + 1][j + 1] = dp[i][j] + 1;
      } else {
        dp[i + 1][j + 1] = Math.max(dp[i + 1][j], dp[i][j + 1]);
      }
    }
  }

  // Backtrack to construct diff
  const diff: DiffLine[] = [];
  let i = m;
  let j = n;

  while (i > 0 || j > 0) {
    if (i > 0 && j > 0 && origLines[i - 1] === modLines[j - 1]) {
      diff.unshift({
        type: "unchanged",
        text: origLines[i - 1],
        origIndex: i,
        modIndex: j,
      });
      i--;
      j--;
    } else if (j > 0 && (i === 0 || dp[i][j - 1] >= dp[i - 1][j])) {
      diff.unshift({
        type: "added",
        text: modLines[j - 1],
        modIndex: j,
      });
      j--;
    } else if (i > 0 && (j === 0 || dp[i][j - 1] < dp[i - 1][j])) {
      diff.unshift({
        type: "removed",
        text: origLines[i - 1],
        origIndex: i,
      });
      i--;
    }
  }

  return diff;
}

export const DiffViewer: React.FC<DiffViewerProps> = ({
  originalText,
  modifiedText,
  originalLabel = "Original Draft (V1)",
  modifiedLabel = "Current Version",
}) => {
  const diffLines = useMemo(() => {
    return computeLineDiff(originalText, modifiedText);
  }, [originalText, modifiedText]);

  const stats = useMemo(() => {
    let added = 0;
    let removed = 0;
    let unchanged = 0;
    for (const l of diffLines) {
      if (l.type === "added") added++;
      else if (l.type === "removed") removed++;
      else unchanged++;
    }
    return { added, removed, unchanged };
  }, [diffLines]);

  return (
    <div
      data-testid="diff-viewer-container"
      className="border border-[var(--ds-border)] rounded-md bg-[var(--ds-background-default)] overflow-hidden shadow-xs"
    >
      {/* Header bar */}
      <div className="bg-[var(--ds-background-subtle)] px-3 py-2 border-b border-[var(--ds-border)] flex items-center justify-between text-xs">
        <div className="flex items-center gap-2">
          <span className="font-semibold text-[var(--ds-text)]">Visual Diff</span>
          <span className="text-[var(--ds-text-subtle)]">
            ({originalLabel} ➔ {modifiedLabel})
          </span>
        </div>
        <div className="flex items-center gap-3 font-mono text-[11px]">
          <span className="text-[var(--ds-text-success)] font-semibold">+{stats.added} added</span>
          <span className="text-[var(--ds-text-danger)] font-semibold">-{stats.removed} removed</span>
        </div>
      </div>

      {/* Diff lines view */}
      <div className="max-h-[380px] overflow-y-auto font-mono text-xs divide-y divide-[var(--ds-border)]/20 bg-[var(--ds-background-default)]">
        {diffLines.length === 0 ? (
          <div className="p-4 text-center text-xs text-[var(--ds-text-subtle)] italic">
            No textual changes detected between versions.
          </div>
        ) : (
          diffLines.map((line, idx) => {
            if (line.type === "added") {
              return (
                <div
                  key={idx}
                  data-testid="diff-line-added"
                  className="px-3 py-1 bg-[var(--ds-background-success)] text-[var(--ds-text-success)] flex items-start gap-2 select-text"
                >
                  <span className="select-none font-bold opacity-75 w-4 text-center">+</span>
                  <span className="break-words flex-1 whitespace-pre-wrap">{line.text || " "}</span>
                </div>
              );
            }
            if (line.type === "removed") {
              return (
                <div
                  key={idx}
                  data-testid="diff-line-removed"
                  className="px-3 py-1 bg-[var(--ds-background-danger)] text-[var(--ds-text-danger)] line-through flex items-start gap-2 select-text"
                >
                  <span className="select-none font-bold opacity-75 w-4 text-center">-</span>
                  <span className="break-words flex-1 whitespace-pre-wrap">{line.text || " "}</span>
                </div>
              );
            }
            return (
              <div
                key={idx}
                data-testid="diff-line-unchanged"
                className="px-3 py-1 text-[var(--ds-text)] bg-transparent flex items-start gap-2 select-text"
              >
                <span className="select-none opacity-30 w-4 text-center"> </span>
                <span className="break-words flex-1 whitespace-pre-wrap">{line.text || " "}</span>
              </div>
            );
          })
        )}
      </div>
    </div>
  );
};
