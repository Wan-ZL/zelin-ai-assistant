import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./app";
import "./styles/tokens.css";
import "./styles/shell.css";
import "./styles/board.css";
import "./styles/animations.css";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
