// Demo-request form on the landing page.
const form = document.getElementById("lead-form");
if (form) {
  const msg = document.getElementById("lead-msg");
  const show = (text, type) => { msg.className = "notice " + type; msg.textContent = text; };
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    if (!form.reportValidity()) return;
    const btn = form.querySelector("button[type=submit]");
    btn.disabled = true; btn.classList.add("busy");
    try {
      const res = await fetch("/api/public/leads", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(new FormData(form))) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(Array.isArray(data.detail) ? data.detail.map((d) => d.msg.replace(/^Value error, /, "")).join(" ") : data.detail || "Something went wrong. Please try again.");
      form.reset();
      show("Thanks! We'll email you within one working day.", "ok");
    } catch (err) { show(err.message, "error"); }
    finally { btn.disabled = false; btn.classList.remove("busy"); }
  });
}
