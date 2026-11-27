"use strict";

const simulationForm = document.getElementById("simulation-form");
const output = document.getElementById("simulation-result");
const earthRadiusMetres = 6378137;
const metreFormat = new Intl.NumberFormat("zh-CN", { maximumFractionDigits: 4 });

function distanceMetres(fromLat, fromLng, toLat, toLng) {
  const toRadians = Math.PI / 180;
  const firstLatitude = fromLat * toRadians;
  const secondLatitude = toLat * toRadians;
  const deltaLatitude = (toLat - fromLat) * toRadians;
  const deltaLongitude = (toLng - fromLng) * toRadians;
  const chord = Math.sin(deltaLatitude / 2) ** 2
    + Math.cos(firstLatitude) * Math.cos(secondLatitude) * Math.sin(deltaLongitude / 2) ** 2;
  return Math.round(2 * earthRadiusMetres * Math.asin(Math.sqrt(Math.min(1, chord))) * 1e4) / 1e4;
}

simulationForm.addEventListener("input", () => { output.hidden = true; });
simulationForm.addEventListener("submit", (event) => {
  event.preventDefault();
  if (!simulationForm.reportValidity()) return;
  const fields = simulationForm.elements;
  const distance = distanceMetres(
    fields.reference_latitude.valueAsNumber, fields.reference_longitude.valueAsNumber,
    fields.point_latitude.valueAsNumber, fields.point_longitude.valueAsNumber,
  );
  const radius = fields.radius.valueAsNumber;
  const inRange = distance <= radius;
  const status = document.createElement("span");
  status.className = "pill";
  status.dataset.tone = inRange ? "positive" : "warning";
  status.textContent = inRange ? "在测试范围内" : "超出测试范围";
  const details = document.createElement("span");
  details.textContent = "距参考点 " + metreFormat.format(distance) + " 米 · 范围 " + metreFormat.format(radius) + " 米";
  output.replaceChildren(status, details);
  output.hidden = false;
});
document.getElementById("calculate-distance").disabled = false;
