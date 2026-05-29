import type { ColumnDef } from "@tanstack/react-table";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DataTable } from "./data-table";

type Row = { name: string; qty: number };

const columns: Array<ColumnDef<Row>> = [
  { accessorKey: "name", header: "Name" },
  { accessorKey: "qty", header: "Qty" },
];

describe("DataTable", () => {
  it("renders header labels and one row per datum", () => {
    render(
      <DataTable
        columns={columns}
        data={[
          { name: "Alpha", qty: 2 },
          { name: "Beta", qty: 5 },
        ]}
      />
    );

    expect(
      screen.getByRole("columnheader", { name: "Name" })
    ).toBeInTheDocument();
    expect(screen.getByText("Alpha")).toBeInTheDocument();
    expect(screen.getByText("Beta")).toBeInTheDocument();
    // header row + 2 data rows
    expect(screen.getAllByRole("row")).toHaveLength(3);
  });

  it("shows the empty message when there are no rows", () => {
    render(<DataTable columns={columns} data={[]} emptyMessage="Nothing here." />);

    expect(screen.getByText("Nothing here.")).toBeInTheDocument();
    // header row + the single empty-state row
    expect(screen.getAllByRole("row")).toHaveLength(2);
  });
});
