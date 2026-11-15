import React from "react";
import ReactDOM from "react-dom/client";
import App from "./App";
import { OmbroProvider } from "./hooks/useOmbro";
import "./styles/base.css";
import "./styles/layout.css";
import "./styles/components.css";
import "./styles/pages.css";

ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
  <React.StrictMode>
    <OmbroProvider>
      <App />
    </OmbroProvider>
  </React.StrictMode>,
);
