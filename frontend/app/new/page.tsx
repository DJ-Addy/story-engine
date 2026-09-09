import type { Metadata } from "next";
import NewProject from "@/components/newproject/NewProject";

export const metadata: Metadata = {
  title: "New project · Story Engine",
  description: "Upload a screenplay or a book and turn it into a story graph.",
};

export default function NewProjectPage() {
  return <NewProject />;
}
