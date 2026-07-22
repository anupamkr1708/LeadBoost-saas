"use client";

import { useMemo, useState } from "react";
import {
  type ColumnDef,
  type SortingState,
  flexRender,
  getCoreRowModel,
  getFilteredRowModel,
  getPaginationRowModel,
  getSortedRowModel,
  useReactTable,
} from "@tanstack/react-table";
import { ArrowUpDown, ChevronLeft, ChevronRight, ExternalLink, MoreHorizontal, RotateCw, Trash2 } from "lucide-react";
import { Checkbox } from "@/components/ui/checkbox";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { QualificationBadge } from "@/components/shared/status-badge";
import { ScoreRing } from "@/components/shared/score-ring";
import { Skeleton } from "@/components/ui/skeleton";
import { EmptyState } from "@/components/shared/empty-state";
import { ConfirmDialog } from "@/components/shared/confirm-dialog";
import { useDeleteLead, useProcessLead } from "@/features/leads/hooks";
import { ensureProtocol, formatDate, getHostname } from "@/lib/utils";
import type { Lead } from "@/types/api";
import { Users } from "lucide-react";

interface LeadsTableProps {
  leads: Lead[];
  loading: boolean;
  onOpenLead: (leadId: number) => void;
}

export function LeadsTable({ leads, loading, onOpenLead }: LeadsTableProps) {
  const [sorting, setSorting] = useState<SortingState>([{ id: "created_at", desc: true }]);
  const [rowSelection, setRowSelection] = useState<Record<string, boolean>>({});
  const [globalFilter, setGlobalFilter] = useState("");
  const [deleteTarget, setDeleteTarget] = useState<Lead | null>(null);

  const deleteLead = useDeleteLead();
  const processLead = useProcessLead();

  const columns = useMemo<ColumnDef<Lead>[]>(
    () => [
      {
        id: "select",
        header: ({ table }) => (
          <Checkbox
            checked={table.getIsAllPageRowsSelected() || (table.getIsSomePageRowsSelected() && "indeterminate")}
            onCheckedChange={(v) => table.toggleAllPageRowsSelected(!!v)}
            onClick={(e) => e.stopPropagation()}
          />
        ),
        cell: ({ row }) => (
          <Checkbox
            checked={row.getIsSelected()}
            onCheckedChange={(v) => row.toggleSelected(!!v)}
            onClick={(e) => e.stopPropagation()}
          />
        ),
        enableSorting: false,
        size: 36,
      },
      {
        accessorKey: "company_name",
        header: "Company",
        cell: ({ row }) => (
          <div className="min-w-0">
            <p className="truncate text-sm font-medium">{row.original.company_name || getHostname(row.original.website)}</p>
            <a
              href={ensureProtocol(row.original.website)}
              target="_blank"
              rel="noreferrer"
              onClick={(e) => e.stopPropagation()}
              className="mt-0.5 flex items-center gap-1 text-xs text-muted-foreground hover:text-primary-300"
            >
              {getHostname(row.original.website)} <ExternalLink className="h-3 w-3" />
            </a>
          </div>
        ),
      },
      {
        accessorKey: "industry",
        header: "Industry",
        cell: ({ getValue }) => <span className="text-sm text-muted-foreground">{(getValue() as string) || "—"}</span>,
      },
      {
        accessorKey: "contact_name",
        header: "Contact",
        cell: ({ row }) => (
          <div className="text-sm">
            <p>{row.original.contact_name || "—"}</p>
            <p className="text-xs text-muted-foreground">{row.original.email || "No email"}</p>
          </div>
        ),
      },
      {
        accessorKey: "qualification_label",
        header: "Qualification",
        cell: ({ getValue }) => <QualificationBadge label={getValue() as string} />,
      },
      {
        accessorKey: "score",
        header: "Score",
        cell: ({ getValue }) => <ScoreRing value={getValue() as number} size={40} strokeWidth={4} />,
      },
      {
        accessorKey: "created_at",
        header: ({ column }) => (
          <button className="flex items-center gap-1" onClick={() => column.toggleSorting(column.getIsSorted() === "asc")}>
            Created <ArrowUpDown className="h-3 w-3" />
          </button>
        ),
        cell: ({ getValue }) => <span className="text-sm text-muted-foreground">{formatDate(getValue() as string)}</span>,
      },
      {
        id: "actions",
        header: "",
        cell: ({ row }) => (
          <DropdownMenu>
            <DropdownMenuTrigger onClick={(e) => e.stopPropagation()} className="flex h-8 w-8 items-center justify-center rounded-lg hover:bg-white/10">
              <MoreHorizontal className="h-4 w-4" />
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" onClick={(e) => e.stopPropagation()}>
              <DropdownMenuItem onClick={() => onOpenLead(row.original.id)}>View details</DropdownMenuItem>
              <DropdownMenuItem onClick={() => processLead.mutate(row.original.id)}>
                <RotateCw className="h-3.5 w-3.5" /> Reprocess
              </DropdownMenuItem>
              <DropdownMenuItem destructive onClick={() => setDeleteTarget(row.original)}>
                <Trash2 className="h-3.5 w-3.5" /> Delete
              </DropdownMenuItem>
            </DropdownMenuContent>
          </DropdownMenu>
        ),
        enableSorting: false,
        size: 40,
      },
    ],
    [onOpenLead, processLead]
  );

  const table = useReactTable({
    data: leads,
    columns,
    state: { sorting, rowSelection, globalFilter },
    onSortingChange: setSorting,
    onRowSelectionChange: setRowSelection,
    onGlobalFilterChange: setGlobalFilter,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getFilteredRowModel: getFilteredRowModel(),
    getPaginationRowModel: getPaginationRowModel(),
    globalFilterFn: (row, _columnId, filterValue) => {
      const lead = row.original;
      const haystack = `${lead.company_name ?? ""} ${lead.website} ${lead.industry ?? ""} ${lead.contact_name ?? ""} ${lead.email ?? ""}`.toLowerCase();
      return haystack.includes(String(filterValue).toLowerCase());
    },
    initialState: { pagination: { pageSize: 10 } },
  });

  const selectedCount = Object.keys(rowSelection).length;

  if (loading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} className="h-14 w-full" />
        ))}
      </div>
    );
  }

  if (leads.length === 0) {
    return <EmptyState icon={Users} title="No leads yet" description="Run a discovery search or add a lead manually to get started." />;
  }

  return (
    <div className="space-y-3">
      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <input
          value={globalFilter}
          onChange={(e) => setGlobalFilter(e.target.value)}
          placeholder="Search company, website, contact…"
          className="h-10 w-full max-w-xs rounded-xl border border-white/10 bg-white/[0.04] px-3.5 text-sm placeholder:text-muted-foreground/70 focus:outline-none focus-visible:ring-2 focus-visible:ring-primary-400"
        />
        {selectedCount > 0 && (
          <div className="flex items-center gap-2 rounded-xl border border-primary-500/30 bg-primary-500/10 px-3 py-1.5 text-sm">
            <span>{selectedCount} selected</span>
            <Button
              size="sm"
              variant="destructive"
              onClick={() => {
                const ids = Object.keys(rowSelection).map((idx) => leads[Number(idx)]?.id).filter(Boolean) as number[];
                ids.forEach((id) => deleteLead.mutate(id));
                setRowSelection({});
              }}
            >
              <Trash2 className="h-3.5 w-3.5" /> Delete
            </Button>
          </div>
        )}
      </div>

      <div className="overflow-x-auto rounded-2xl border border-white/[0.06]">
        <table className="w-full text-left">
          <thead className="border-b border-white/[0.06] bg-white/[0.02] text-xs font-medium uppercase tracking-wide text-muted-foreground">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((header) => (
                  <th key={header.id} className="whitespace-nowrap px-4 py-3">
                    {flexRender(header.column.columnDef.header, header.getContext())}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody className="divide-y divide-white/[0.05]">
            {table.getRowModel().rows.map((row) => (
              <tr
                key={row.id}
                onClick={() => onOpenLead(row.original.id)}
                className="cursor-pointer transition-colors hover:bg-white/[0.03]"
              >
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="px-4 py-3">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <div className="flex items-center justify-between text-sm text-muted-foreground">
        <span>
          Page {table.getState().pagination.pageIndex + 1} of {Math.max(table.getPageCount(), 1)}
        </span>
        <div className="flex items-center gap-2">
          <Button variant="secondary" size="sm" onClick={() => table.previousPage()} disabled={!table.getCanPreviousPage()}>
            <ChevronLeft className="h-3.5 w-3.5" /> Prev
          </Button>
          <Button variant="secondary" size="sm" onClick={() => table.nextPage()} disabled={!table.getCanNextPage()}>
            Next <ChevronRight className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>

      <ConfirmDialog
        open={!!deleteTarget}
        onOpenChange={(o) => !o && setDeleteTarget(null)}
        title="Delete this lead?"
        description={`This will remove "${deleteTarget?.company_name || deleteTarget?.website}" from your leads. This can't be undone.`}
        confirmLabel="Delete lead"
        destructive
        loading={deleteLead.isPending}
        onConfirm={() => {
          if (deleteTarget) deleteLead.mutate(deleteTarget.id);
          setDeleteTarget(null);
        }}
      />
    </div>
  );
}
