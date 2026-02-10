import { Pool, PoolConfig } from "pg";

const globalForDb = global as unknown as { pool: Pool | undefined };

// Validate required environment variables
const requiredEnvVars = [
  "AZURE_POSTGRES_HOST",
  "AZURE_POSTGRES_DB",
  "AZURE_POSTGRES_USER",
  "AZURE_POSTGRES_PASSWORD",
] as const;

for (const envVar of requiredEnvVars) {
  if (!process.env[envVar]) {
    throw new Error(
      `Missing required environment variable: ${envVar}. Please check your .env.local file.`,
    );
  }
}

// Configure SSL for Azure PostgreSQL
// Azure requires SSL in production, but we can be more lenient in development
const sslConfig =
  process.env.NODE_ENV === "production"
    ? {
        rejectUnauthorized: true,
        // Azure PostgreSQL uses a well-known CA, so we don't need to provide a custom cert
      }
    : {
        rejectUnauthorized: false,
      };

// Pool configuration optimized for Vercel serverless environment
const poolConfig: PoolConfig = {
  host: process.env.AZURE_POSTGRES_HOST,
  port: parseInt(process.env.AZURE_POSTGRES_PORT || "5432"),
  database: process.env.AZURE_POSTGRES_DB,
  user: process.env.AZURE_POSTGRES_USER,
  password: process.env.AZURE_POSTGRES_PASSWORD,
  ssl: sslConfig,
  // Serverless-optimized connection pooling
  max: 2, // Lower max connections for serverless (Vercel has many function instances)
  idleTimeoutMillis: 30000, // 30 seconds
  connectionTimeoutMillis: 10000, // 10 seconds (increased for Azure)
  // Enable keep-alive for Azure PostgreSQL
  keepAlive: true,
  keepAliveInitialDelayMillis: 10000,
};

export const pool = globalForDb.pool || new Pool(poolConfig);

if (process.env.NODE_ENV !== "production") globalForDb.pool = pool;

export async function query<T = unknown>(
  text: string,
  params?: unknown[],
): Promise<T[]> {
  const client = await pool.connect();
  try {
    const result = await client.query(text, params);
    return result.rows;
  } finally {
    client.release();
  }
}

export default pool;
