import ChatWorkspace from "@/components/chat-workspace";
import AuthGate from "@/components/auth-context";

export default function HomePage() {
  return <AuthGate><ChatWorkspace /></AuthGate>;
}
