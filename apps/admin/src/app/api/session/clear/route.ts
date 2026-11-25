import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE } from "@/lib/access";

/** Drops a cookie that Appwrite no longer accepts (expired session), then returns to login. */
export function GET(req: NextRequest) {
  const res = NextResponse.redirect(new URL("/login?expired=1", req.url));
  res.cookies.delete(SESSION_COOKIE);
  return res;
}
