import { redirect } from "next/navigation";

type AdminRedirectPageProps = {
  params: {
    slug?: string[];
  };
};

export default function AdminRedirectPage({ params }: AdminRedirectPageProps) {
  const slug = params.slug || [];
  if (slug.length === 0 || slug[0] === "dashboard") {
    redirect("/dashboard/overview");
  }
  if (slug[0] === "admins") {
    redirect(`/dashboard/therapists/${slug.slice(1).join("/")}`.replace(/\/$/, ""));
  }
  redirect(`/dashboard/${slug.join("/")}`);
}
