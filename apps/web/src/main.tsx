import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
import { installInferenceFetchCache } from "./inferenceFetchCache";
import "./styles.css";

installInferenceFetchCache();

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
