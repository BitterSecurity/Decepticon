import { requireAuth, AuthError } from "@/lib/auth-bridge";
import { NextResponse } from "next/server";

export async function GET() {
  try {
    await requireAuth();
    const response = await fetch(new URL("/metrics", process.env.BLUE_MONITOR_URL ?? "http://blue-monitor:8085"), {
      signal: AbortSignal.timeout(3000),
      cache: "no-store",
    });
    if (!response.ok) return NextResponse.json({ available: false }, { status: 503 });
    return NextResponse.json({ available: true });
  } catch (error) {
    if (error instanceof AuthError) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    return NextResponse.json({ available: false }, { status: 503 });
  }
}
