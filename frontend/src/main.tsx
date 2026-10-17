import React from "react";
import { createRoot } from "react-dom/client";
import App from "./App";
const element = document.getElementById("root");
if (!element) throw new Error("ROOT_ELEMENT_MISSING");
createRoot(element).render(<React.StrictMode><App /></React.StrictMode>);
