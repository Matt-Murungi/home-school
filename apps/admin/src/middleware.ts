import { NextResponse, type NextRequest } from "next/server";
import { SESSION_COOKIE, decideAccess } from "@/lib/access";

// Cheap gate only: cookie presence. The layout validates the session with Appwrite on every request.
export function middleware(req: NextRequest) {
  const d = decideAccess({
    pathname: req.nextUrl.pathname,
    search: req.nextUrl.search,
    hasSession: !!req.cookies.get(SESSION_COOKIE)?.value,
  });
  if (d.action === "redirect") return NextResponse.redirect(new URL(d.to, req.url));
  const headers = new Headers(req.headers);
  headers.set("x-pathname", req.nextUrl.pathname);
  return NextResponse.next({ request: { headers } });
}

export const config = { matcher: ["/((?!_next/static|_next/image|favicon.ico).*)"] };
