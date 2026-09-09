"use client";

import Link from "next/link";

function renderInline(text: string, projectId: number) {
  return text.split(/(workspace:\/\/[A-Za-z0-9_./-]+|`[^`]+`|\*\*[^*]+\*\*)/g).map((part, index) => {
    if (part.startsWith("workspace://")) {
      return <Link className="font-mono text-blue-400 underline hover:text-blue-300" href={`/projects/${projectId}/workspace?file=${encodeURIComponent(part.slice(12))}`} key={index}>{part}</Link>;
    }
    if (part.startsWith("`") && part.endsWith("`")) {
      return <code className="rounded bg-white/10 px-1 py-0.5 font-mono text-[0.9em] text-cyan-200" key={index}>{part.slice(1, -1)}</code>;
    }
    if (part.startsWith("**") && part.endsWith("**")) {
      return <strong key={index}>{part.slice(2, -2)}</strong>;
    }
    return <span key={index}>{part}</span>;
  });
}

function renderMarkdown(text: string, projectId: number) {
  return text.split("\n").map((line, index) => {
    const heading = line.match(/^(#{1,6})\s+(.+)$/);
    if (heading) {
      const level = Math.min(heading[1].length, 4);
      const Heading = ["h1", "h2", "h3", "h4"][level - 1] as "h1" | "h2" | "h3" | "h4";
      const className = level === 1 ? "text-xl font-bold" : level === 2 ? "text-lg font-bold" : "text-base font-semibold";
      return <Heading className={`${className} mt-3 first:mt-0 text-gray-100`} key={index}>{renderInline(heading[2], projectId)}</Heading>;
    }
    const bullet = line.match(/^\s*[-*]\s+(.+)$/);
    if (bullet) return <div className="flex gap-2 pl-3" key={index}><span className="text-blue-400">•</span><span>{renderInline(bullet[1], projectId)}</span></div>;
    const numbered = line.match(/^\s*(\d+)\.\s+(.+)$/);
    if (numbered) return <div className="flex gap-2 pl-1" key={index}><span className="min-w-5 text-right text-blue-400">{numbered[1]}.</span><span>{renderInline(numbered[2], projectId)}</span></div>;
    return <div className="min-h-[1.25em] whitespace-pre-wrap leading-relaxed" key={index}>{renderInline(line, projectId)}</div>;
  });
}

export default function MessageContent({
  content,
  projectId,
  isStreaming = false,
}: {
  content: string;
  projectId: number;
  isStreaming?: boolean;
}) {
  if (!content && !isStreaming) return null;

  const parts = content.split(/```([\w-]*)\n([\s\S]*?)```/g);
  return (
    <div className="space-y-2">
      {parts.map((part, index) => {
        if (index % 3 === 1) return null;
        if (index % 3 === 2) {
          const language = parts[index - 1] || "text";
          return (
            <div className="group relative my-2" key={index}>
              <span className="absolute right-12 top-2 text-[10px] uppercase text-gray-400 font-mono">
                {language}
              </span>
              <button
                onClick={() => void navigator.clipboard.writeText(part)}
                className="absolute right-2 top-2 rounded bg-white/10 px-2 py-1 text-xs text-gray-300 transition-colors hover:bg-white/20 active:scale-95"
              >
                Copy
              </button>
              <pre className="overflow-x-auto rounded-xl bg-gray-950 p-4 pt-8 font-mono text-xs text-gray-100 dark:bg-black/80">
                <code>{part}</code>
              </pre>
            </div>
          );
        }
        const isLast = index === parts.length - 1 || (index === parts.length - 2 && !parts[parts.length - 1]);
        return (
          <div key={index}>
            {renderMarkdown(part, projectId)}
            {isStreaming && isLast && (
              <span className="ml-1 inline-block h-4 w-1.5 translate-y-0.5 rounded-sm bg-blue-500 animate-pulse" />
            )}
          </div>
        );
      })}
    </div>
  );
}
