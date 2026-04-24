"use client";

import { useState, useEffect, useCallback } from "react";
import {
  socialClient,
  type RedditPost,
  type RedditStats,
  type NewsArticle,
  type NewsFeed,
} from "@/lib/api/socialClient";
import SocialMediaView from "./SocialMediaView";

export default function SocialMediaContainer() {
  const [activeTab, setActiveTab] = useState<"reddit" | "news">("news");

  // Reddit state
  const [redditStats, setRedditStats] = useState<RedditStats | null>(null);
  const [redditPosts, setRedditPosts] = useState<RedditPost[]>([]);
  const [redditLoading, setRedditLoading] = useState(false);
  const [redditError, setRedditError] = useState<string | null>(null);
  const [filterSub, setFilterSub] = useState("");
  const [filterKeyword, setFilterKeyword] = useState("");
  const [sortBy, setSortBy] = useState<"score" | "date">("score");

  // News state
  const [newsStats, setNewsStats] = useState<{
    total_articles: number;
    feeds: { name: string; count: number }[];
    sources: { name: string; count: number }[];
  } | null>(null);
  const [newsArticles, setNewsArticles] = useState<NewsArticle[]>([]);
  const [newsFeeds, setNewsFeeds] = useState<NewsFeed[]>([]);
  const [newsLoading, setNewsLoading] = useState(false);
  const [newsError, setNewsError] = useState<string | null>(null);
  const [newsFeed, setNewsFeed] = useState("");
  const [newsKeyword, setNewsKeyword] = useState("");

  // Fetch Reddit
  const fetchReddit = useCallback(async () => {
    setRedditLoading(true);
    setRedditError(null);
    try {
      const params: Record<string, string | number> = { sort: sortBy, limit: 200 };
      if (filterSub) params.subreddit = filterSub;
      if (filterKeyword) params.keyword = filterKeyword;
      const [stats, posts] = await Promise.all([
        socialClient.getStats(),
        socialClient.getPosts(params as Parameters<typeof socialClient.getPosts>[0]),
      ]);
      setRedditStats(stats);
      setRedditPosts(posts.posts);
    } catch (err) {
      setRedditError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setRedditLoading(false);
    }
  }, [filterSub, filterKeyword, sortBy]);

  // Fetch News
  const fetchNews = useCallback(async () => {
    setNewsLoading(true);
    setNewsError(null);
    try {
      const params: Record<string, string | number> = {};
      if (newsFeed) { params.feed = newsFeed; params.limit = 200; }
      else if (newsKeyword) { params.keyword = newsKeyword; params.limit = 200; }
      const [stats, articles, feeds] = await Promise.all([
        socialClient.getNewsStats(),
        socialClient.getNewsArticles(params as Parameters<typeof socialClient.getNewsArticles>[0]),
        socialClient.getNewsFeeds(),
      ]);
      setNewsStats(stats);
      setNewsArticles(articles.articles);
      setNewsFeeds(feeds.feeds);
    } catch (err) {
      setNewsError(err instanceof Error ? err.message : "Failed to load");
    } finally {
      setNewsLoading(false);
    }
  }, [newsFeed, newsKeyword]);

  useEffect(() => {
    if (activeTab === "reddit") fetchReddit();
    else fetchNews();
  }, [activeTab, fetchReddit, fetchNews]);

  return (
    <SocialMediaView
      activeTab={activeTab}
      onTabChange={setActiveTab}
      // Reddit
      redditStats={redditStats}
      redditPosts={redditPosts}
      redditLoading={redditLoading}
      redditError={redditError}
      filterSub={filterSub}
      filterKeyword={filterKeyword}
      sortBy={sortBy}
      onFilterSubChange={setFilterSub}
      onFilterKeywordChange={setFilterKeyword}
      onSortChange={setSortBy}
      // News
      newsStats={newsStats}
      newsArticles={newsArticles}
      newsFeeds={newsFeeds}
      newsLoading={newsLoading}
      newsError={newsError}
      newsFeed={newsFeed}
      newsKeyword={newsKeyword}
      onNewsFeedChange={setNewsFeed}
      onNewsKeywordChange={setNewsKeyword}
    />
  );
}
