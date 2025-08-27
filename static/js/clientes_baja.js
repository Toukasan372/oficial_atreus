// static/js/clientes_baja.js
(function () {
  function onClick(e) {
    const btn = e.target.closest("button[data-action]");
    if (!btn) return;

    const action = btn.getAttribute("data-action");
    const id = btn.getAttribute("data-id");
    if (!id) return;

    if (action === "baja") {
      if (!confirm("¿Seguro que deseas dar de baja este cliente?")) return;
      fetch(`/clientes/${id}/baja`, {
        method: "POST",
        headers: { "Content-Type": "application/json" }
      })
      .then(r => r.json())
      .then(res => {
        if (!res.success) throw new Error(res.error || "Error al dar baja");
        // Actualiza la UI: recarga o marca como baja
        location.reload(); // simple y efectivo
      })
      .catch(err => alert(err.message));
    }

    if (action === "reactivar") {
      if (!confirm("¿Reactivar este cliente?")) return;
      fetch(`/clientes/${id}/reactivar`, {
        method: "POST",
        headers: { "Content-Type": "application/json" }
      })
      .then(r => r.json())
      .then(res => {
        if (!res.success) throw new Error(res.error || "Error al reactivar");
        location.reload();
      })
      .catch(err => alert(err.message));
    }
  }

  document.addEventListener("click", onClick, false);
})();
