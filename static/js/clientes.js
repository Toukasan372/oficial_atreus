// static/js/clientes.js
document.addEventListener("DOMContentLoaded", () => {
  const form = document.getElementById("form-nuevo-cliente");
  if (!form) return;

  form.addEventListener("submit", async (e) => {
    e.preventDefault();

    const payload = {
      nombre_negocio: document.getElementById("cli-nombre").value.trim(),
      estado: document.getElementById("cli-estado").value, // string
      observaciones: document.getElementById("cli-observaciones").value.trim(),
      direccion: {
        calle: document.getElementById("cli-calle").value.trim(),
        municipio: document.getElementById("cli-municipio").value.trim(),
        provincia: document.getElementById("cli-provincia").value.trim(),
        principal: true
      }
    };

    try {
      const res = await fetch("/clientes/nuevo", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      const data = await res.json().catch(() => ({}));

      if (!res.ok || !data.success) {
        alert("No se pudo guardar el cliente.\n" + (data.error || res.statusText));
        return;
      }

      // Éxito: cierra modal, refresca lista o lo que necesites
      alert("Cliente creado!");
      // window.location.reload();
    } catch (err) {
      console.error(err);
      alert("Error de red al guardar el cliente.");
    }
  });
});
