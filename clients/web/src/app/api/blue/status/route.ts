import { requireAuth, AuthError } from "@/lib/auth-bridge";
import { NextResponse } from "next/server";

export async function GET() {
  try {
    await requireAuth();
    const [monitor, sensor] = await Promise.all([
      fetch(new URL("/metrics", process.env.BLUE_MONITOR_URL ?? "http://blue-monitor:8085"), {
        signal: AbortSignal.timeout(3000), cache: "no-store",
      }),
      fetch(new URL("/metrics", process.env.BLUE_SENSOR_URL ?? "http://blue-receiver:8082"), {
        signal: AbortSignal.timeout(3000), cache: "no-store",
      }),
    ]);
    if (!monitor.ok || !sensor.ok) return NextResponse.json({ available: false }, { status: 503 });
    return NextResponse.json({ available: true });
  } catch (error) {
    if (error instanceof AuthError) return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    return NextResponse.json({ available: false }, { status: 503 });
  }
}
