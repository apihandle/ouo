import { NextRequest, NextResponse } from "next/server";
import { desc } from "drizzle-orm";
import { db } from "@/db";
import { conversations } from "@/db/schema";

export async function GET() {
  const rows = await db
    .select()
    .from(conversations)
    .orderBy(desc(conversations.updatedAt))
    .limit(100);
  return NextResponse.json({ conversations: rows });
}

export async function POST(req: NextRequest) {
  let title = "New chat";
  try {
    const body = await req.json();
    if (typeof body?.title === "string" && body.title.trim()) {
      title = body.title.trim().slice(0, 120);
    }
  } catch {
    // empty body is fine
  }
  const [row] = await db.insert(conversations).values({ title }).returning();
  return NextResponse.json({ conversation: row }, { status: 201 });
}
