"use client";

import React, { useState } from "react";
import {
  MessageSquare,
  Newspaper,
  ExternalLink,
  Search,
  Loader2,
  AlertTriangle,
  MessageCircle,
  ThumbsUp,
} from "lucide-react";
import type { RedditPost, RedditStats, NewsArticle } from "@/lib/api/socialClient";

interface SocialMediaViewProps {
  activeTab: "reddit" | "news";
  onTabChange: (tab: "reddit" | "news") => void;
  // Reddit
  redditStats: RedditStats | null;
  redditPosts: RedditPost[];
  redditLoading: boolean;
  redditError: string | null;
  filterSub: string;
  filterKeyword: string;
  sortBy: "score" | "date";
  onFilterSubChange: (sub: string) => void;
  onFilterKeywordChange: (kw: string) => void;
  onSortChange: (sort: "score" | "date") => void;
  // News
  newsStats: { total_articles: number; feeds: { name: string; count: number }[]; sources: { name: string; count: number }[] } | null;
  newsArticles: NewsArticle[];
  newsLoading: boolean;
  newsError: string | null;
  newsFeed: string;
  newsKeyword: string;
  onNewsFeedChange: (feed: string) => void;
  onNewsKeywordChange: (kw: string) => void;
}

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="bg-white rounded-lg border border-slate-200 px-4 py-3">
      <p className="text-[10px] font-semibold text-slate-400 uppercase tracking-wider">{label}</p>
      <p className="text-xl font-bold text-slate-900 mt-0.5">{value}</p>
      {sub && <p className="text-[10px] text-slate-500 mt-0.5">{sub}</p>}
    </div>
  );
}

function scoreColor(score: number) {
  if (score >= 1000) return "text-orange-600";
  if (score >= 100) return "text-amber-600";
  if (score >= 10) return "text-slate-700";
  return "text-slate-400";
}

function timeAgo(dateStr: string) {
  if (!dateStr) return "";
  const d = new Date(dateStr.replace(" ", "T") + (dateStr.includes("T") ? "" : ":00Z"));
  const diff = (Date.now() - d.getTime()) / 1000;
  if (isNaN(diff) || diff < 0) return dateStr.slice(0, 10);
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  if (diff < 604800) return `${Math.floor(diff / 86400)}d ago`;
  return dateStr.slice(0, 10);
}

function LoadingState() {
  return (
    <div className="flex items-center justify-center h-40">
      <Loader2 size={20} className="animate-spin text-slate-400" />
      <span className="ml-2 text-sm text-slate-500">Loading...</span>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Reddit Panel
// ---------------------------------------------------------------------------

function RedditPanel({
  stats, posts, loading, error,
  filterSub, filterKeyword, sortBy,
  onFilterSubChange, onFilterKeywordChange, onSortChange,
}: {
  stats: RedditStats | null;
  posts: RedditPost[];
  loading: boolean;
  error: string | null;
  filterSub: string;
  filterKeyword: string;
  sortBy: "score" | "date";
  onFilterSubChange: (sub: string) => void;
  onFilterKeywordChange: (kw: string) => void;
  onSortChange: (sort: "score" | "date") => void;
}) {
  const [searchInput, setSearchInput] = useState(filterKeyword);
  const handleSearch = (e: React.FormEvent) => { e.preventDefault(); onFilterKeywordChange(searchInput); };

  return (
    <>
      {/* Filters */}
      <div className="flex items-center gap-3 px-8 py-3 bg-white border-b border-slate-100 shrink-0 flex-wrap">
        <form onSubmit={handleSearch} className="flex items-center gap-1">
          <div className="relative">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
            <input type="text" value={searchInput} onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search posts..." className="h-8 pl-8 pr-3 w-56 text-xs border border-slate-200 rounded-md focus:outline-none focus:ring-1 focus:ring-indigo-400" />
          </div>
          <button type="submit" className="h-8 px-3 text-xs font-medium bg-indigo-600 text-white rounded-md hover:bg-indigo-700">Search</button>
          {filterKeyword && <button onClick={() => { setSearchInput(""); onFilterKeywordChange(""); }} className="h-8 px-2 text-xs text-red-500 hover:bg-red-50 rounded-md border border-red-200">Clear</button>}
        </form>
        <div className="flex items-center gap-1">
          <span className="text-[10px] font-semibold text-slate-400 uppercase">Sub:</span>
          <button onClick={() => onFilterSubChange("")} className={`h-7 px-2 rounded text-[10px] font-medium transition-colors ${!filterSub ? "bg-indigo-600 text-white" : "text-slate-600 hover:bg-slate-100 border border-slate-200"}`}>All</button>
          {stats?.subreddits.slice(0, 8).map((s) => (
            <button key={s.name} onClick={() => onFilterSubChange(filterSub === s.name ? "" : s.name)}
              className={`h-7 px-2 rounded text-[10px] font-medium transition-colors ${filterSub === s.name ? "bg-indigo-600 text-white" : "text-slate-600 hover:bg-slate-100 border border-slate-200"}`}>
              r/{s.name}
            </button>
          ))}
        </div>
        <div className="flex items-center bg-slate-100 rounded-md p-0.5 ml-auto">
          <button onClick={() => onSortChange("score")} className={`h-6 px-2 rounded text-[10px] font-semibold ${sortBy === "score" ? "bg-white text-slate-800 shadow-sm" : "text-slate-500"}`}>Top Score</button>
          <button onClick={() => onSortChange("date")} className={`h-6 px-2 rounded text-[10px] font-semibold ${sortBy === "date" ? "bg-white text-slate-800 shadow-sm" : "text-slate-500"}`}>Latest</button>
        </div>
      </div>

      {error && <div className="flex items-center gap-2 px-8 py-2 bg-red-50 border-b border-red-200 text-[11px] text-red-700 shrink-0"><AlertTriangle size={13} />{error}</div>}

      <div className="flex-1 overflow-y-auto px-8 py-6 space-y-4 bg-slate-50">
        {loading ? <LoadingState /> : (
          <>
            {stats && (
              <div className="grid grid-cols-4 gap-3">
                <StatCard label="Total Posts" value={stats.total_posts.toLocaleString()} />
                <StatCard label="Subreddits" value={stats.subreddits.length} />
                <StatCard label="Keywords" value={stats.keywords.length} />
                <StatCard label="Showing" value={posts.length} sub={filterSub ? `r/${filterSub}` : filterKeyword ? `"${filterKeyword}"` : "All"} />
              </div>
            )}
            {stats && stats.keywords.length > 0 && (
              <div className="flex items-center gap-1 flex-wrap">
                <span className="text-[10px] font-semibold text-slate-400 uppercase mr-1">Keywords:</span>
                {stats.keywords.map((kw) => (
                  <button key={kw.keyword} onClick={() => { setSearchInput(kw.keyword); onFilterKeywordChange(kw.keyword); }}
                    className={`h-6 px-2 rounded text-[10px] font-medium border ${filterKeyword === kw.keyword ? "bg-indigo-50 border-indigo-300 text-indigo-700" : "bg-white border-slate-200 text-slate-600 hover:bg-slate-50"}`}>
                    {kw.keyword} <span className="ml-1 opacity-50">{kw.count}</span>
                  </button>
                ))}
              </div>
            )}
            <div className="space-y-2">
              {posts.map((post) => (
                <div key={post.id} className="bg-white rounded-lg border border-slate-200 px-4 py-3 hover:border-slate-300 transition-colors">
                  <div className="flex items-start gap-3">
                    <div className="flex flex-col items-center shrink-0 w-12 pt-0.5">
                      <ThumbsUp size={12} className={scoreColor(post.score)} />
                      <span className={`text-sm font-bold ${scoreColor(post.score)}`}>{post.score >= 1000 ? `${(post.score / 1000).toFixed(1)}k` : post.score}</span>
                    </div>
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-[10px] font-semibold text-indigo-600 bg-indigo-50 px-1.5 py-0.5 rounded">r/{post.subreddit}</span>
                        {post.flair && <span className="text-[10px] text-slate-500 bg-slate-100 px-1.5 py-0.5 rounded">{post.flair}</span>}
                        <span className="text-[10px] text-slate-400">{timeAgo(post.created_date)}</span>
                        {post.query && <span className="text-[10px] text-amber-600 bg-amber-50 px-1.5 py-0.5 rounded">{post.query}</span>}
                      </div>
                      <a href={post.permalink} target="_blank" rel="noopener noreferrer" className="text-sm font-medium text-slate-900 hover:text-indigo-600 leading-snug">
                        {post.title}<ExternalLink size={10} className="inline ml-1 opacity-30" />
                      </a>
                      {post.selftext && <p className="text-[11px] text-slate-500 mt-1 line-clamp-2">{post.selftext}</p>}
                      <div className="flex items-center gap-3 mt-1.5">
                        <span className="flex items-center gap-1 text-[10px] text-slate-400"><MessageCircle size={10} />{post.num_comments} comments</span>
                        <span className="text-[10px] text-slate-400">{Math.round(post.upvote_ratio * 100)}% upvoted</span>
                      </div>
                    </div>
                  </div>
                </div>
              ))}
              {posts.length === 0 && !loading && <div className="text-center py-12 text-sm text-slate-500">No posts found.</div>}
            </div>
          </>
        )}
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// News Panel
// ---------------------------------------------------------------------------

// Feed section ordering — follows the AI value chain top-down
const FEED_SECTIONS: { key: string; label: string; accent: string; borderColor: string }[] = [
  // --- AI Layer ---
  { key: "AI Models & Updates", label: "AI Models & Updates", accent: "text-violet-700", borderColor: "border-violet-600" },
  { key: "AI Products & Applications", label: "AI Products & Applications", accent: "text-purple-700", borderColor: "border-purple-500" },
  { key: "AI Funding / VC / IPO / M&A", label: "AI Funding / VC / IPO / M&A", accent: "text-fuchsia-700", borderColor: "border-fuchsia-500" },
  { key: "AI Startup Revenue & ARR", label: "AI Startup Revenue & ARR", accent: "text-fuchsia-600", borderColor: "border-fuchsia-400" },
  // --- Infrastructure Layer ---
  { key: "Neocloud & CSP", label: "Neocloud & CSP", accent: "text-indigo-700", borderColor: "border-indigo-600" },
  { key: "Datacenter Deals & Capex", label: "Datacenter Deals & Capex", accent: "text-indigo-600", borderColor: "border-indigo-500" },
  { key: "Datacenter Cooling", label: "Datacenter Cooling", accent: "text-cyan-700", borderColor: "border-cyan-600" },
  { key: "AI Power & Energy", label: "AI Power & Energy", accent: "text-amber-700", borderColor: "border-amber-500" },
  // --- Compute & Chips ---
  { key: "GPU Datacenter (NVIDIA)", label: "GPU Datacenter (NVIDIA)", accent: "text-green-700", borderColor: "border-green-600" },
  { key: "ASIC & Custom Silicon", label: "ASIC & Custom Silicon", accent: "text-lime-700", borderColor: "border-lime-600" },
  // --- Supply Chain Layer ---
  { key: "Supply Chain: Memory (DRAM/HBM/NAND)", label: "Memory: DRAM / HBM / NAND", accent: "text-green-600", borderColor: "border-green-500" },
  { key: "Supply Chain: Interconnect & Optical", label: "Interconnect & Optical", accent: "text-teal-700", borderColor: "border-teal-500" },
  { key: "Supply Chain: Advanced Packaging", label: "Advanced Packaging", accent: "text-emerald-700", borderColor: "border-emerald-500" },
  { key: "Foundry & Semi Equipment", label: "Foundry & Semi Equipment", accent: "text-sky-700", borderColor: "border-sky-500" },
  { key: "Supply Chain: Materials & Chemicals", label: "Materials & Chemicals", accent: "text-stone-700", borderColor: "border-stone-500" },
  { key: "Supply Chain: Labor & Construction", label: "Labor & Construction", accent: "text-stone-600", borderColor: "border-stone-400" },
  { key: "Semi Company Earnings", label: "Semi Company Earnings", accent: "text-blue-700", borderColor: "border-blue-600" },
  // --- Regional Layer ---
  { key: "Taiwan Semi (English)", label: "Taiwan Semi", accent: "text-emerald-700", borderColor: "border-emerald-600" },
  { key: "Taiwan Semi (Chinese)", label: "Taiwan Semi (CN)", accent: "text-emerald-600", borderColor: "border-emerald-400" },
  { key: "Japan Semi (English)", label: "Japan Semi", accent: "text-pink-700", borderColor: "border-pink-600" },
  { key: "Japan Semi (Japanese)", label: "Japan Semi (JP)", accent: "text-pink-600", borderColor: "border-pink-400" },
  { key: "Korea Semi (English)", label: "Korea Semi", accent: "text-sky-700", borderColor: "border-sky-600" },
  { key: "Korea Semi (Korean)", label: "Korea Semi (KR)", accent: "text-sky-600", borderColor: "border-sky-400" },
  // --- Regulation & Macro Layer ---
  { key: "Regulation: Tariffs & Trade", label: "Tariffs & Trade Policy", accent: "text-red-700", borderColor: "border-red-600" },
  { key: "Regulation: AI Policy", label: "AI Regulation & Policy", accent: "text-orange-700", borderColor: "border-orange-500" },
  { key: "Regulation: Healthcare & Biotech", label: "Healthcare & Biotech Regulation", accent: "text-rose-600", borderColor: "border-rose-400" },
  { key: "Macro: Geopolitics & Trade", label: "Geopolitics & Trade", accent: "text-red-600", borderColor: "border-red-500" },
  { key: "Macro: Economy & Markets", label: "Economy & Markets", accent: "text-amber-600", borderColor: "border-amber-500" },
];

const ARTICLES_PER_SECTION = 20;

function FeedSection({
  section,
  articles,
  expanded,
  onToggleExpand,
}: {
  section: typeof FEED_SECTIONS[0];
  articles: NewsArticle[];
  expanded: boolean;
  onToggleExpand: () => void;
}) {
  const shown = expanded ? articles : articles.slice(0, ARTICLES_PER_SECTION);

  return (
    <div className="bg-white rounded-lg border border-slate-200 overflow-hidden">
      {/* Section header */}
      <div className={`border-l-[3px] ${section.borderColor} px-4 py-2.5 border-b border-slate-100 flex items-center justify-between`}>
        <h3 className={`text-xs font-bold uppercase tracking-wide ${section.accent}`}>
          {section.label}
          <span className="ml-2 text-[10px] font-normal text-slate-400">{articles.length}</span>
        </h3>
        {articles.length > ARTICLES_PER_SECTION && (
          <button
            onClick={onToggleExpand}
            className="text-[10px] text-indigo-600 hover:text-indigo-800 font-medium"
          >
            {expanded ? "Show less" : `Show all ${articles.length}`}
          </button>
        )}
      </div>

      {/* Articles */}
      <div className="divide-y divide-slate-50">
        {shown.map((article, idx) => (
          <div key={article.guid || idx} className="px-4 py-2 hover:bg-slate-50 transition-colors">
            <div className="flex items-start gap-2">
              <div className="flex-1 min-w-0">
                <a
                  href={article.link}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="text-[13px] font-medium text-slate-900 hover:text-indigo-600 transition-colors leading-snug block"
                >
                  {article.title_en || article.title}
                </a>
                {article.title_en && article.title !== article.title_en && (
                  <p className="text-[10px] text-slate-400 mt-0.5 italic leading-snug">
                    {article.title}
                  </p>
                )}
              </div>
              <div className="flex items-center gap-2 shrink-0 pt-0.5">
                <span className={`text-[10px] ${article.source_tier === 1 ? "text-blue-600 font-semibold" : "text-slate-400"}`}>
                  {article.source_name}
                </span>
                <span className="text-[10px] text-slate-300">
                  {timeAgo(article.pub_date)}
                </span>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function NewsPanel({
  stats, articles, loading, error,
  feed, keyword, onFeedChange, onKeywordChange,
}: {
  stats: SocialMediaViewProps["newsStats"];
  articles: NewsArticle[];
  loading: boolean;
  error: string | null;
  feed: string;
  keyword: string;
  onFeedChange: (f: string) => void;
  onKeywordChange: (kw: string) => void;
}) {
  const [searchInput, setSearchInput] = useState(keyword);
  const [expandedSections, setExpandedSections] = useState<Set<string>>(new Set());
  const handleSearch = (e: React.FormEvent) => { e.preventDefault(); onKeywordChange(searchInput); };

  const toggleExpand = (key: string) => {
    setExpandedSections((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  // Group articles by feed_label
  const grouped = new Map<string, NewsArticle[]>();
  for (const a of articles) {
    const key = a.feed_label;
    if (!grouped.has(key)) grouped.set(key, []);
    grouped.get(key)!.push(a);
  }

  // Filter sections that have articles, in defined order
  const activeSections = FEED_SECTIONS.filter(
    (s) => grouped.has(s.key) && (grouped.get(s.key)?.length ?? 0) > 0,
  );

  // Any feeds not in FEED_SECTIONS definition
  const extraFeeds = [...grouped.keys()].filter(
    (k) => !FEED_SECTIONS.some((s) => s.key === k),
  );

  // If keyword/feed filter is active, show flat list instead
  const showFlat = !!keyword || !!feed;

  return (
    <>
      {/* Search bar */}
      <div className="flex items-center gap-3 px-8 py-3 bg-white border-b border-slate-100 shrink-0 flex-wrap">
        <form onSubmit={handleSearch} className="flex items-center gap-1">
          <div className="relative">
            <Search size={13} className="absolute left-2.5 top-1/2 -translate-y-1/2 text-slate-400" />
            <input type="text" value={searchInput} onChange={(e) => setSearchInput(e.target.value)}
              placeholder="Search articles..." className="h-8 pl-8 pr-3 w-64 text-xs border border-slate-200 rounded-md focus:outline-none focus:ring-1 focus:ring-indigo-400" />
          </div>
          <button type="submit" className="h-8 px-3 text-xs font-medium bg-indigo-600 text-white rounded-md hover:bg-indigo-700">Search</button>
          {keyword && <button onClick={() => { setSearchInput(""); onKeywordChange(""); }} className="h-8 px-2 text-xs text-red-500 hover:bg-red-50 rounded-md border border-red-200">Clear</button>}
        </form>
        {feed && (
          <div className="flex items-center gap-1">
            <span className="text-[10px] text-slate-500">Filtered:</span>
            <span className="text-[10px] font-semibold text-indigo-700 bg-indigo-50 px-2 py-0.5 rounded">{feed}</span>
            <button onClick={() => onFeedChange("")} className="text-[10px] text-red-500 hover:underline">clear</button>
          </div>
        )}
        {stats && !feed && !keyword && (
          <span className="ml-auto text-[10px] text-slate-400">
            {stats.total_articles.toLocaleString()} articles across {stats.feeds.length} feeds
          </span>
        )}
      </div>

      {error && <div className="flex items-center gap-2 px-8 py-2 bg-red-50 border-b border-red-200 text-[11px] text-red-700 shrink-0"><AlertTriangle size={13} />{error}</div>}

      <div className="flex-1 overflow-y-auto px-8 py-6 bg-slate-50">
        {loading ? <LoadingState /> : showFlat ? (
          /* Flat list when searching or filtering */
          <div className="space-y-2">
            {articles.map((article, idx) => (
              <div key={article.guid || idx} className="bg-white rounded-lg border border-slate-200 px-4 py-3 hover:border-slate-300 transition-colors">
                <div className="flex items-start gap-2">
                  <div className="flex-1 min-w-0">
                    <a href={article.link} target="_blank" rel="noopener noreferrer"
                      className="text-sm font-medium text-slate-900 hover:text-indigo-600 leading-snug">
                      {article.title_en || article.title}
                      <ExternalLink size={10} className="inline ml-1 opacity-30" />
                    </a>
                    {article.title_en && article.title !== article.title_en && (
                      <p className="text-[10px] text-slate-400 mt-0.5 italic">{article.title}</p>
                    )}
                    <div className="flex items-center gap-2 mt-1">
                      <span className="text-[10px] text-indigo-600 font-medium">{article.feed_label}</span>
                      <span className={`text-[10px] ${article.source_tier === 1 ? "text-blue-600 font-semibold" : "text-slate-400"}`}>{article.source_name}</span>
                      <span className="text-[10px] text-slate-300">{timeAgo(article.pub_date)}</span>
                    </div>
                  </div>
                </div>
              </div>
            ))}
            {articles.length === 0 && <div className="text-center py-12 text-sm text-slate-500">No articles found.</div>}
          </div>
        ) : (
          /* Section grid — newspaper layout */
          <div className="grid grid-cols-2 gap-4">
            {activeSections.map((section) => (
              <FeedSection
                key={section.key}
                section={section}
                articles={grouped.get(section.key) ?? []}
                expanded={expandedSections.has(section.key)}
                onToggleExpand={() => toggleExpand(section.key)}
              />
            ))}
            {extraFeeds.map((feedName) => (
              <FeedSection
                key={feedName}
                section={{ key: feedName, label: feedName, accent: "text-slate-700", borderColor: "border-slate-400" }}
                articles={grouped.get(feedName) ?? []}
                expanded={expandedSections.has(feedName)}
                onToggleExpand={() => toggleExpand(feedName)}
              />
            ))}
          </div>
        )}
      </div>
    </>
  );
}

// ---------------------------------------------------------------------------
// Main View
// ---------------------------------------------------------------------------

export default function SocialMediaView(props: SocialMediaViewProps) {
  return (
    <div className="flex flex-col h-full overflow-hidden">
      {/* Header */}
      <div className="flex items-center justify-between px-8 py-4 bg-white border-b border-slate-200 shrink-0">
        <div className="flex items-center gap-3">
          {props.activeTab === "news" ? (
            <Newspaper size={22} className="text-indigo-600 shrink-0" />
          ) : (
            <MessageSquare size={22} className="text-indigo-600 shrink-0" />
          )}
          <div>
            <h1 className="text-xl font-bold text-slate-900 leading-tight">Social Media</h1>
            <p className="text-xs text-slate-500 mt-0.5">News & community sentiment tracking for semiconductors</p>
          </div>
        </div>

        {/* Tab toggle */}
        <div className="flex items-center bg-slate-100 rounded-lg p-0.5">
          <button onClick={() => props.onTabChange("news")}
            className={`h-7 px-3 rounded-md text-xs font-semibold transition-colors flex items-center gap-1.5 ${props.activeTab === "news" ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-700"}`}>
            <Newspaper size={13} /> News
          </button>
          <button onClick={() => props.onTabChange("reddit")}
            className={`h-7 px-3 rounded-md text-xs font-semibold transition-colors flex items-center gap-1.5 ${props.activeTab === "reddit" ? "bg-white text-slate-900 shadow-sm" : "text-slate-500 hover:text-slate-700"}`}>
            <MessageSquare size={13} /> Reddit
          </button>
        </div>
      </div>

      {/* Active panel */}
      {props.activeTab === "reddit" ? (
        <RedditPanel
          stats={props.redditStats}
          posts={props.redditPosts}
          loading={props.redditLoading}
          error={props.redditError}
          filterSub={props.filterSub}
          filterKeyword={props.filterKeyword}
          sortBy={props.sortBy}
          onFilterSubChange={props.onFilterSubChange}
          onFilterKeywordChange={props.onFilterKeywordChange}
          onSortChange={props.onSortChange}
        />
      ) : (
        <NewsPanel
          stats={props.newsStats}
          articles={props.newsArticles}
          loading={props.newsLoading}
          error={props.newsError}
          feed={props.newsFeed}
          keyword={props.newsKeyword}
          onFeedChange={props.onNewsFeedChange}
          onKeywordChange={props.onNewsKeywordChange}
        />
      )}
    </div>
  );
}
