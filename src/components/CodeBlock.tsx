"use client";

import { useState } from "react";
import { Copy, Check } from "lucide-react";

export function CodeBlock({
  code,
  codeClassName,
}: {
  code: string;
  codeClassName?: string;
}) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(code);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <div className="group relative">
      <pre className="overflow-x-auto rounded-lg bg-gray-50 p-3">
        <code className={`font-mono text-xs text-gray-700 ${codeClassName ?? ""}`}>
          {code}
        </code>
      </pre>
      <button
        onClick={handleCopy}
        className="absolute top-2 right-2 flex items-center gap-1 rounded-md border border-gray-200 bg-white px-2 py-1 text-xs font-medium text-gray-600 opacity-0 transition-opacity hover:bg-gray-50 group-hover:opacity-100"
        aria-label="Copy code"
      >
        {copied ? (
          <Check className="h-3 w-3 text-green-500" />
        ) : (
          <Copy className="h-3 w-3" />
        )}
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}
