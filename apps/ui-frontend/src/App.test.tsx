import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import App from "./App";

test("台本・音声管理タブで制作ダッシュボードが表示される", () => {
  render(
    <MemoryRouter initialEntries={["/dashboard"]}>
      <App />
    </MemoryRouter>
  );
  expect(screen.getByText(/ダッシュボード/)).toBeInTheDocument();
});
