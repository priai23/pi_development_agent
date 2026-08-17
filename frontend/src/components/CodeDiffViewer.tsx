"use client";

import { useState } from "react";
import { Copy, Check, FileCode, GitCompare, WrapText } from "lucide-react";

interface CodeDiffViewerProps {
  path: string;
  content: string;
  isDiff?: boolean;
}

export default function CodeDiffViewer({ path, content, isDiff = false }: CodeDiffViewerProps) {
  const [copied, setCopied] = useState(false);
  const [wrap, setWrap] = useState(false);

  const handleCopy = () => {
    void navigator.clipboard.writeText(content);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  const getLanguage = (filepath: string) => {
    if (filepath.endsWith(".py")) return "Python";
    if (filepath.endsWith(".xml")) return "XML";
    if (filepath.endsWith(".js") || filepath.endsWith(".ts") || filepath.endsWith(".tsx")) return "TypeScript";
    if (filepath.endsWith(".json")) return "JSON";
    if (filepath.endsWith(".csv")) return "CSV";
    return "Text";
  };

  const allLines = content.split("\n");
  const diffTruncated = isDiff && allLines.length > 5000;
  const lines = diffTruncated ? allLines.slice(0, 5000) : allLines;

  return (
    <div className="flex h-full flex-col bg-zinc-950 text-xs font-mono">
      {/* File Header Bar */}
      <div className="flex items-center justify-between border-b border-white/10 bg-zinc-900 px-4 py-2 text-gray-300">
        <div className="flex items-center gap-2 truncate">
          {isDiff ? <GitCompare className="h-3.5 w-3.5 text-blue-400" /> : <FileCode className="h-3.5 w-3.5 text-purple-400" />}
          <span className="truncate font-semibold text-white">{path || "Select a file"}</span>
          {path && (
            <span className="rounded bg-white/10 px-1.5 py-0.5 text-[10px] uppercase text-gray-400">
              {getLanguage(path)}
            </span>
          )}
        </div>
        {content && (
          <div className="flex items-center gap-2">
            <button
              onClick={() => setWrap(!wrap)}
              aria-label="Toggle line wrapping"
              className={`rounded p-1 transition ${wrap ? "bg-blue-600 text-white" : "text-gray-400 hover:bg-white/10 hover:text-white"}`}
              title="Toggle line wrapping"
            >
              <WrapText className="h-3.5 w-3.5" />
            </button>
            <button
              onClick={handleCopy}
              className="flex items-center gap-1 rounded bg-white/5 px-2 py-1 text-[11px] text-gray-300 transition hover:bg-white/10 hover:text-white"
            >
              {copied ? <Check className="h-3.5 w-3.5 text-emerald-400" /> : <Copy className="h-3.5 w-3.5" />}
              {copied ? "Copied" : "Copy"}
            </button>
          </div>
        )}
      </div>

      {/* Code Container */}
      <div className="flex-1 overflow-auto p-2">
        {!content ? (
          <div className="flex h-full items-center justify-center text-gray-500">
            Select a file from the workspace tree to review.
          </div>
        ) : isDiff ? (
          <div className="divide-y divide-white/5">
            {diffTruncated && <p className="sticky top-0 z-10 bg-amber-950 p-2 text-amber-200">Showing the first 5,000 of {allLines.length.toLocaleString()} lines. Copy includes the complete diff.</p>}
            {lines.map((line, idx) => {
              const isAdd = line.startsWith("+");
              const isDel = line.startsWith("-");
              const isHeader = line.startsWith("@@") || line.startsWith("diff ");
              return (
                <div
                  key={idx}
                  className={`flex px-2 py-0.5 ${
                    isAdd
                      ? "bg-emerald-950/40 text-emerald-300"
                      : isDel
                      ? "bg-red-950/40 text-red-400"
                      : isHeader
                      ? "bg-blue-950/40 font-bold text-blue-400"
                      : "text-gray-300"
                  }`}
                >
                  <span className="w-8 shrink-0 select-none text-right pr-3 text-gray-600 font-mono text-[10px]">
                    {idx + 1}
                  </span>
                  <pre className={`flex-1 font-mono text-xs ${wrap ? "whitespace-pre-wrap break-all" : "whitespace-pre"}`}>
                    {line}
                  </pre>
                </div>
              );
            })}
          </div>
        ) : (
          <pre className={`p-2 font-mono text-xs leading-5 text-gray-200 ${wrap ? "whitespace-pre-wrap break-all" : "whitespace-pre"}`}>{content}</pre>
        )}
      </div>
    </div>
  );
}
