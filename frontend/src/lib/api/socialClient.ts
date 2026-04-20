/**
 * socialClient -- Reddit social media data.
 */

import { apiRequest } from "./base";

export interface RedditPost {
  id: string;
  subreddit: string;
  title: string;
  selftext: string;
  author: string;
  score: number;
  upvote_ratio: number;
  num_comments: number;
  permalink: string;
  created_date: string;
  flair: string;
  query: string;
}

export interface SubredditCount {
  name: string;
  count: number;
}

export interface KeywordCount {
  keyword: string;
  count: number;
}

export interface RedditStats {
  total_posts: number;
  subreddits: SubredditCount[];
  keywords: KeywordCount[];
  date_range: { min: string | null; max: string | null } | null;
}

export const socialClient = {
  async getStats(): Promise<RedditStats> {
    return apiRequest<RedditStats>("/social/reddit/stats");
  },

  async getPosts(params?: {
    subreddit?: string;
    keyword?: string;
    sort?: "score" | "date";
    limit?: number;
  }): Promise<{ posts: RedditPost[]; total: number }> {
    const qs = new URLSearchParams();
    if (params?.subreddit) qs.set("subreddit", params.subreddit);
    if (params?.keyword) qs.set("keyword", params.keyword);
    if (params?.sort) qs.set("sort", params.sort);
    if (params?.limit) qs.set("limit", String(params.limit));
    const q = qs.toString();
    return apiRequest<{ posts: RedditPost[]; total: number }>(
      `/social/reddit/posts${q ? `?${q}` : ""}`,
    );
  },

  async getTrending(
    days = 7,
    limit = 30,
  ): Promise<{ posts: RedditPost[] }> {
    return apiRequest<{ posts: RedditPost[] }>(
      `/social/reddit/trending?days=${days}&limit=${limit}`,
    );
  },

  // Google News
  async getNewsStats(): Promise<{
    total_articles: number;
    feeds: { name: string; count: number }[];
    sources: { name: string; count: number }[];
    date_range: { min: string | null; max: string | null } | null;
  }> {
    return apiRequest("/social/news/stats");
  },

  async getNewsArticles(params?: {
    feed?: string;
    keyword?: string;
    source?: string;
    limit?: number;
  }): Promise<{ articles: NewsArticle[]; total: number }> {
    const qs = new URLSearchParams();
    if (params?.feed) qs.set("feed", params.feed);
    if (params?.keyword) qs.set("keyword", params.keyword);
    if (params?.source) qs.set("source", params.source);
    if (params?.limit) qs.set("limit", String(params.limit));
    const q = qs.toString();
    return apiRequest(`/social/news/articles${q ? `?${q}` : ""}`);
  },
};

export interface NewsArticle {
  title: string;
  title_en?: string;
  link: string;
  pub_date: string;
  source_name: string;
  feed_label: string;
  guid: string;
  source_tier?: number;
}
