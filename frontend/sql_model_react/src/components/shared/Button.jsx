import React from "react";

export default function Button({ children, variant = "secondary", size = "md", className = "", ...props }) {
  return (
    <button className={`btn btn-${variant} btn-${size} ${className}`.trim()} {...props}>
      {children}
    </button>
  );
}
