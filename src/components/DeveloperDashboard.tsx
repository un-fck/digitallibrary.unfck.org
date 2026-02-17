"use client";

import { useState, useEffect, useCallback } from "react";
import {
  Key,
  Copy,
  Check,
  RefreshCw,
  BarChart3,
  Zap,
  Code2,
  ExternalLink,
  Loader2,
} from "lucide-react";

interface KeyData {
  api_key?: string;
  key_prefix: string;
  tier: string;
  rate_limit: number;
  created_at?: string;
  last_used_at?: string | null;
  is_new: boolean;
}

interface UsageData {
  requests_today: number;
  requests_this_month: number;
  daily: { day: string; count: number }[];
}

const TIER_LABELS: Record<string, string> = {
  free: "Free",
  research: "Research",
  institutional: "Institutional",
};

const DAILY_LIMITS: Record<string, number> = {
  free: 10_000,
  research: 100_000,
  institutional: -1,
};

function SectionCard({
  icon: Icon,
  title,
  children,
}: {
  icon: React.ElementType;
  title: string;
  children: React.ReactNode;
}) {
  return (
    <div className="rounded-xl border border-gray-200 bg-white p-5 shadow-sm">
      <div className="mb-4 flex items-center gap-2">
        <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-un-blue/10">
          <Icon className="h-4 w-4 text-un-blue" />
        </div>
        <h3 className="text-sm font-semibold text-gray-900">{title}</h3>
      </div>
      {children}
    </div>
  );
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);

  const handleCopy = () => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <button
      onClick={handleCopy}
      className="flex items-center gap-1 rounded-md border border-gray-200 px-2.5 py-1.5 text-xs font-medium text-gray-600 transition-colors hover:bg-gray-50"
    >
      {copied ? (
        <>
          <Check className="h-3 w-3 text-green-500" />
          Copied
        </>
      ) : (
        <>
          <Copy className="h-3 w-3" />
          Copy
        </>
      )}
    </button>
  );
}

function UsageBar({ used, limit }: { used: number; limit: number }) {
  if (limit <= 0) return <span className="text-xs text-gray-400">Unlimited</span>;
  const pct = Math.min(100, (used / limit) * 100);
  const color = pct > 80 ? "bg-red-400" : pct > 50 ? "bg-yellow-400" : "bg-un-blue";
  return (
    <div className="flex items-center gap-3">
      <div className="h-2 flex-1 rounded-full bg-gray-100">
        <div className={`h-2 rounded-full ${color}`} style={{ width: `${pct}%` }} />
      </div>
      <span className="shrink-0 text-xs text-gray-500">
        {used.toLocaleString()} / {limit.toLocaleString()}
      </span>
    </div>
  );
}

export function DeveloperDashboard() {
  const [keyData, setKeyData] = useState<KeyData | null>(null);
  const [usage, setUsage] = useState<UsageData | null>(null);
  const [loading, setLoading] = useState(true);
  const [rotating, setRotating] = useState(false);
  const [showFullKey, setShowFullKey] = useState(false);

  const fetchKey = useCallback(async () => {
    const res = await fetch("/api/developer/key");
    if (res.ok) {
      const data = await res.json();
      setKeyData(data);
      if (data.is_new && data.api_key) setShowFullKey(true);
    }
  }, []);

  const fetchUsage = useCallback(async () => {
    const res = await fetch("/api/developer/usage");
    if (res.ok) setUsage(await res.json());
  }, []);

  useEffect(() => {
    Promise.all([fetchKey(), fetchUsage()]).finally(() => setLoading(false));
  }, [fetchKey, fetchUsage]);

  const handleRotate = async () => {
    if (!confirm("This will revoke your current key and generate a new one. Continue?")) return;
    setRotating(true);
    const res = await fetch("/api/developer/key", { method: "POST" });
    if (res.ok) {
      const data = await res.json();
      setKeyData(data);
      setShowFullKey(true);
    }
    setRotating(false);
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center py-20">
        <Loader2 className="h-6 w-6 animate-spin text-un-blue" />
      </div>
    );
  }

  const apiKey = keyData?.api_key;
  const dailyLimit = DAILY_LIMITS[keyData?.tier || "free"] || 10_000;

  return (
    <div className="space-y-6">
      <div>
        <h2 className="text-xl font-bold tracking-tight text-gray-900">Developer Dashboard</h2>
        <p className="mt-1 text-sm text-gray-500">Manage your API key and monitor usage</p>
      </div>

      <div className="grid gap-6 lg:grid-cols-2">
        {/* API Key */}
        <SectionCard icon={Key} title="API Key">
          {showFullKey && apiKey ? (
            <div className="space-y-3">
              <div className="rounded-lg border border-yellow-200 bg-yellow-50 p-3">
                <p className="mb-2 text-xs font-medium text-yellow-800">
                  Save this key — it won&apos;t be shown again
                </p>
                <code className="block break-all rounded-md bg-white p-2 font-mono text-xs text-gray-800">
                  {apiKey}
                </code>
              </div>
              <div className="flex items-center gap-2">
                <CopyButton text={apiKey} />
                <button
                  onClick={() => setShowFullKey(false)}
                  className="text-xs text-gray-400 hover:text-gray-600"
                >
                  Dismiss
                </button>
              </div>
            </div>
          ) : (
            <div className="space-y-3">
              <div className="flex items-center justify-between">
                <code className="rounded-md bg-gray-100 px-3 py-1.5 font-mono text-sm text-gray-700">
                  {keyData?.key_prefix}...{'*'.repeat(20)}
                </code>
                <button
                  onClick={handleRotate}
                  disabled={rotating}
                  className="flex items-center gap-1 rounded-md border border-gray-200 px-2.5 py-1.5 text-xs font-medium text-gray-600 transition-colors hover:bg-gray-50 disabled:opacity-50"
                >
                  <RefreshCw className={`h-3 w-3 ${rotating ? "animate-spin" : ""}`} />
                  Rotate
                </button>
              </div>
              {keyData?.created_at && (
                <p className="text-xs text-gray-400">
                  Created {new Date(keyData.created_at).toLocaleDateString()}
                  {keyData.last_used_at && (
                    <> · Last used {new Date(keyData.last_used_at).toLocaleDateString()}</>
                  )}
                </p>
              )}
            </div>
          )}
        </SectionCard>

        {/* Plan */}
        <SectionCard icon={Zap} title="Plan">
          <div className="space-y-3">
            <div className="flex items-center justify-between">
              <span className="inline-flex items-center rounded-full bg-un-blue/10 px-3 py-1 text-sm font-semibold text-un-blue">
                {TIER_LABELS[keyData?.tier || "free"]}
              </span>
              <span className="text-sm text-gray-500">{keyData?.rate_limit} req/min</span>
            </div>
            <div>
              <p className="mb-1 text-xs font-medium text-gray-500">Daily requests</p>
              <UsageBar used={usage?.requests_today || 0} limit={dailyLimit} />
            </div>
          </div>
        </SectionCard>

        {/* Usage */}
        <SectionCard icon={BarChart3} title="Usage">
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-4">
              <div>
                <p className="text-2xl font-bold text-gray-900">
                  {(usage?.requests_today || 0).toLocaleString()}
                </p>
                <p className="text-xs text-gray-400">Today</p>
              </div>
              <div>
                <p className="text-2xl font-bold text-gray-900">
                  {(usage?.requests_this_month || 0).toLocaleString()}
                </p>
                <p className="text-xs text-gray-400">This month</p>
              </div>
            </div>
            {usage?.daily && usage.daily.length > 0 && (
              <div className="flex items-end gap-1 h-16">
                {usage.daily.map((d) => {
                  const maxCount = Math.max(...usage.daily.map((x) => x.count), 1);
                  const height = Math.max(4, (d.count / maxCount) * 56);
                  return (
                    <div
                      key={d.day}
                      className="flex-1 rounded-t bg-un-blue/20 transition-all hover:bg-un-blue/40"
                      style={{ height: `${height}px` }}
                      title={`${d.day}: ${d.count} requests`}
                    />
                  );
                })}
              </div>
            )}
          </div>
        </SectionCard>

        {/* Quick Start */}
        <SectionCard icon={Code2} title="Quick Start">
          <div className="space-y-3">
            <div>
              <p className="mb-1 text-xs font-medium text-gray-500">curl</p>
              <div className="flex items-start justify-between gap-2 rounded-lg bg-gray-50 p-3">
                <code className="break-all font-mono text-xs text-gray-700">
                  curl -H &quot;Authorization: Bearer {keyData?.key_prefix}...&quot; \<br />
                  &nbsp;&nbsp;http://localhost:8000/v1/documents?q=A/RES
                </code>
              </div>
            </div>
            <div>
              <p className="mb-1 text-xs font-medium text-gray-500">Python</p>
              <div className="rounded-lg bg-gray-50 p-3">
                <code className="block font-mono text-xs text-gray-700">
                  <span className="text-purple-600">import</span> httpx<br />
                  r = httpx.get(<span className="text-green-700">&quot;http://localhost:8000/v1/search&quot;</span>,<br />
                  &nbsp;&nbsp;params={'{'}&quot;q&quot;: &quot;climate change&quot;{'}'},<br />
                  &nbsp;&nbsp;headers={'{'}&quot;Authorization&quot;: f&quot;Bearer {'{'}<span className="text-blue-600">KEY</span>{'}'}&quot;{'}'})<br />
                  docs = r.json()[<span className="text-green-700">&quot;results&quot;</span>]
                </code>
              </div>
            </div>
            <a
              href="/v1/docs"
              target="_blank"
              rel="noopener noreferrer"
              className="inline-flex items-center gap-1 text-sm font-medium text-un-blue hover:underline"
            >
              API Documentation
              <ExternalLink className="h-3 w-3" />
            </a>
          </div>
        </SectionCard>
      </div>
    </div>
  );
}
