/* Render stored venue photos and generated, per-place demo artwork. */
async function getPlaceImage(place) {
  const providedUrl = String(place?.image_url || "").trim();
  if (/^\/media\/[a-z0-9/_-]+\.(?:jpe?g|png|webp|gif)$/i.test(providedUrl)) {
    return {
      url: providedUrl,
      source: place?.image_source_url || null,
      sourceName: place?.image_source_name || "",
      license: place?.image_license || ""
    };
  }
  if (/^https:\/\//i.test(providedUrl) && /^https:\/\//i.test(String(place?.image_source_url || ""))) {
    return {
      url: providedUrl,
      source: place.image_source_url,
      sourceName: place.image_source_name || "Ảnh địa điểm",
      license: place.image_license || ""
    };
  }
  if (/^data:image\/svg\+xml;base64,[a-z0-9+/=]+$/i.test(providedUrl)) {
    return { url: providedUrl, source: null, sourceName: null, license: "" };
  }
  return null;
}

async function loadPlaceImages(root = document) {
  const nodes = [...root.querySelectorAll("img[data-place-image]")];
  if (!nodes.length) return;

  // Avoid sending dozens of requests at once.
  let index = 0;
  const worker = async () => {
    while (index < nodes.length) {
      const node = nodes[index++];
      const place = {
        name: node.dataset.name || "",
        province: node.dataset.province || "",
        category: node.dataset.category || "",
        image_url: node.dataset.imageUrl || "",
        image_source_url: node.dataset.imageSourceUrl || "",
        image_source_name: node.dataset.imageSourceName || "",
        image_license: node.dataset.imageLicense || ""
      };
      const image = await getPlaceImage(place);
      const wrapper = node.closest(".place-image, .place-photo, .activity-photo, .hotel-photo");
      const credit = wrapper?.querySelector(".image-credit");
      const placeholder = wrapper?.querySelector(".photo-status");
      if (!image) {
        node.removeAttribute("src");
        node.classList.remove("is-loaded");
        node.classList.add("image-error");
        if (placeholder) placeholder.textContent = typeof getLang === "function" && getLang() === "en" ? "No verified photo" : "Chưa có ảnh xác thực";
        credit?.remove();
        continue;
      }
      node.onload = () => node.classList.add("is-loaded");
      node.onerror = () => node.classList.remove("is-loaded");
      node.src = image.url;
      node.dataset.source = image.source || "";
      node.alt = `Ảnh ${place.name}`;
      if (node.complete && node.naturalWidth > 0) node.classList.add("is-loaded");
      if (credit) {
        credit.textContent = [image.sourceName, image.license].filter(Boolean).join(" · ") || "";
        if (image.source) {
          credit.href = image.source;
          credit.target = "_blank";
          credit.rel = "noopener noreferrer";
          credit.style.pointerEvents = "auto";
        } else {
          credit.removeAttribute("href");
          credit.style.pointerEvents = "none";
        }
      }
    }
  };

  await Promise.all([worker(), worker(), worker(), worker()]);
}
