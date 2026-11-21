import { drizzle } from "drizzle-orm/postgres-js";
import postgres from "postgres";

const connectionString =
  process.env.DATABASE_URL ?? "postgres://ubuntu:ubuntu@localhost:5432/app";

const client = postgres(connectionString, { max: 10 });
export const db = drizzle(client);
