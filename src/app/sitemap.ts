import type { MetadataRoute } from "next";

const BASE_URL = "https://digitallibrary.unfck.org";

export default function sitemap(): MetadataRoute.Sitemap {
  return [
    {
      url: BASE_URL,
      changeFrequency: "daily",
      priority: 1,
    },
    {
      url: `${BASE_URL}/docs`,
      changeFrequency: "monthly",
      priority: 0.9,
    },
  ];
}
