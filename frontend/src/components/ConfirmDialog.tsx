"use client";

import { useEffect, useRef } from "react";

export default function ConfirmDialog({ open, title, description, confirmLabel = "Delete", onConfirm, onClose }: { open: boolean; title: string; description: string; confirmLabel?: string; onConfirm: () => void; onClose: () => void }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const dialog = ref.current;
    if (open && dialog && !dialog.open) dialog.showModal();
    if (!open && dialog?.open) dialog.close();
  }, [open]);
  return <dialog ref={ref} onClose={onClose} className="m-auto w-[min(26rem,calc(100%-2rem))] rounded-2xl border border-white/10 bg-zinc-950 p-6 text-white shadow-2xl backdrop:bg-black/70"><h2 className="text-lg font-semibold">{title}</h2><p className="mt-2 text-sm text-gray-400">{description}</p><div className="mt-5 flex justify-end gap-2"><button onClick={() => ref.current?.close()} className="rounded-lg border px-4 py-2">Cancel</button><button onClick={() => { onConfirm(); ref.current?.close(); }} className="rounded-lg bg-red-600 px-4 py-2">{confirmLabel}</button></div></dialog>;
}
