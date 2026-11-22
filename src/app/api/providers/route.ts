import { NextResponse } from "next/server";
import { defaultProviderId, listProviders } from "@/lib/model";

export const dynamic = "force-dynamic";

export async function GET() {
  return NextResponse.json({
    providers: listProviders(),
    defaultProvider: defaultProviderId(),
  });
}
