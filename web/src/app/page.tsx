import { redirect } from "next/navigation";
import { ChatWorkspace } from "@/components/ChatWorkspace";
import { getAuthorizedUser } from "@/lib/supabase/server";

export default async function HomePage() {
  const user = await getAuthorizedUser();
  if (!user) {
    redirect("/login");
  }

  return <ChatWorkspace userEmail={user.email ?? "Authorized User"} />;
}
