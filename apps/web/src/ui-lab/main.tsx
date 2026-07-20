import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import UiLabApp from "./UiLabApp";
import "./ui-lab.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <UiLabApp />
  </StrictMode>,
);
