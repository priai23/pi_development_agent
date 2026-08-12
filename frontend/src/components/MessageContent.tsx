"use client";

import Link from "next/link";

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
          <div className="whitespace-pre-wrap leading-relaxed" key={index}>
            {part.split(/(workspace:\/\/[A-Za-z0-9_./-]+)/g).map((text, partIndex) =>
              text.startsWith("workspace://") ? (
                <Link
                  className="font-mono text-blue-600 underline hover:text-blue-700 dark:text-blue-400"
                  href={`/projects/${projectId}/workspace?file=${encodeURIComponent(text.slice(12))}`}
                  key={partIndex}
                >
                  {text}
                </Link>
              ) : (
                <span key={partIndex}>{text}</span>
              )
            )}
            {isStreaming && isLast && (
              <span className="ml-1 inline-block h-4 w-1.5 translate-y-0.5 rounded-sm bg-blue-500 animate-pulse" />
            )}
          </div>
        );
      })}
    </div>
  );
}
