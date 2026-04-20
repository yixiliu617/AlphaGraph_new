/**
 * pricingClient -- PCPartPicker & CamelCamelCamel price trends.
 */

import { apiRequest } from "./base";

export interface PricingRow {
  category: string;
  component: string;
  month: string;
  date: string;
  avg_price_usd: number;
}

export interface PricingCategory {
  category: string;
  components: string[];
}

export interface CamelProduct {
  asin: string;
  product_name: string;
  quarters: number;
  lowest?: number;
  highest?: number;
  current?: number;
}

export interface GpuPriceRow {
  gpu_name: string;
  market_type: string;
  min_price: number;
  max_price: number;
  median_price: number;
  mean_price: number;
  num_offers: number;
  providers: string;
  timestamp: string;
}

export interface GpuHistoryRow {
  gpu_name: string;
  date: string;
  hour: string;
  min_price: number;
  median_price: number;
  num_offers: number;
}

export interface CamelRow {
  asin: string;
  product_name: string;
  quarter: string;
  approx_price_usd: number;
}

export const pricingClient = {
  async getCategories(): Promise<{ categories: PricingCategory[]; has_weekly: boolean }> {
    return apiRequest<{ categories: PricingCategory[]; has_weekly: boolean }>(
      "/pricing/trends/categories",
    );
  },

  async getTrends(
    category?: string,
    component?: string,
    granularity: "monthly" | "weekly" = "monthly",
  ): Promise<PricingRow[]> {
    const params = new URLSearchParams();
    if (category) params.set("category", category);
    if (component) params.set("component", component);
    params.set("granularity", granularity);
    const res = await apiRequest<{ rows: PricingRow[] }>(
      `/pricing/trends?${params.toString()}`,
    );
    return res.rows;
  },

  async getCamelProducts(): Promise<CamelProduct[]> {
    const res = await apiRequest<{ products: CamelProduct[] }>(
      "/pricing/camel/products",
    );
    return res.products;
  },

  async getGpuLatest(gpu?: string): Promise<GpuPriceRow[]> {
    const qs = gpu ? `?gpu=${encodeURIComponent(gpu)}` : "";
    const res = await apiRequest<{ rows: GpuPriceRow[] }>(`/pricing/gpu/latest${qs}`);
    return res.rows;
  },

  async getGpuHistory(gpu?: string, market = "on_demand"): Promise<GpuHistoryRow[]> {
    const params = new URLSearchParams({ market });
    if (gpu) params.set("gpu", gpu);
    const res = await apiRequest<{ rows: GpuHistoryRow[] }>(`/pricing/gpu/history?${params}`);
    return res.rows;
  },

  async getCamelData(asin?: string): Promise<CamelRow[]> {
    const qs = asin ? `?asin=${asin}` : "";
    const res = await apiRequest<{ rows: CamelRow[] }>(
      `/pricing/camel${qs}`,
    );
    return res.rows;
  },
};
