import { useState } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { CcValuePicker } from '../src/components/CcValuePicker';

function Harness() {
  const initial = (window as any).pickerTest;
  const [value, setValue] = useState(initial.value);
  const [open, setOpen] = useState(true);
  return <>
    <input id="result" value={value} readOnly />
    {open && <CcValuePicker {...{ columnId: 7, columnLabel: 'Shelf', hierarchical: false,
      multiple: initial.multiple, value, onApply: setValue, onClose: () => setOpen(false) }} />}
  </>;
}

createRoot(document.getElementById('root')!).render(
  <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <Harness />
  </QueryClientProvider>,
);
