// Añadir y quitar filas en las listas del formulario.
document.addEventListener("click", (e) => {
  const anadir = e.target.closest("[data-anadir]");
  if (anadir) {
    const lista = document.getElementById(anadir.dataset.anadir);
    const nueva = lista.querySelector(".item").cloneNode(true);
    nueva.querySelectorAll("input, textarea").forEach((el) => (el.value = ""));
    lista.appendChild(nueva);
    nueva.querySelector("input, textarea").focus();
    return;
  }
  const quitar = e.target.closest(".quitar");
  if (quitar) {
    const item = quitar.closest(".item");
    const lista = item.parentElement;
    if (lista.children.length > 1) item.remove();
    else item.querySelectorAll("input, textarea").forEach((el) => (el.value = ""));
  }
});
