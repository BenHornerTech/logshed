import React, { useEffect, useState } from 'react';
import { Server, Plus, Trash2, Edit2, Check, AlertCircle } from 'lucide-react';
import { HostAlias } from '../../types.ts';
import { fetchAliases, saveAlias, deleteAlias } from '../../api/aliases.ts';

interface HostAliasManagerProps {
  initialAddIp?: string | null;
  onAliasSaved?: () => void;
}

export const HostAliasManager: React.FC<HostAliasManagerProps> = ({
  initialAddIp,
  onAliasSaved,
}) => {
  const [aliases, setAliases] = useState<HostAlias[]>([]);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const [ip, setIp] = useState<string>(initialAddIp || '');
  const [alias, setAlias] = useState<string>('');
  const [notes, setNotes] = useState<string>('');
  const [isSaving, setIsSaving] = useState<boolean>(false);
  const [editingIp, setEditingIp] = useState<string | null>(null);

  useEffect(() => {
    if (initialAddIp) {
      setIp(initialAddIp);
      const existing = aliases.find((a) => a.ip === initialAddIp);
      if (existing) {
        setEditingIp(existing.ip);
        setAlias(existing.alias);
        setNotes(existing.notes || '');
      }
    }
  }, [initialAddIp, aliases]);

  const loadAliases = async () => {
    try {
      setIsLoading(true);
      setErrorMsg(null);
      const list = await fetchAliases();
      setAliases(list);
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to load host aliases.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    loadAliases();
  }, []);

  const handleSave = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!ip.trim() || !alias.trim()) return;

    try {
      setIsSaving(true);
      setErrorMsg(null);
      await saveAlias({
        ip: ip.trim(),
        alias: alias.trim(),
        notes: notes.trim() || null,
      });
      setIp('');
      setAlias('');
      setNotes('');
      setEditingIp(null);
      await loadAliases();
      if (onAliasSaved) onAliasSaved();
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to save host alias mapping.');
    } finally {
      setIsSaving(false);
    }
  };

  const handleEdit = (item: HostAlias) => {
    setEditingIp(item.ip);
    setIp(item.ip);
    setAlias(item.alias);
    setNotes(item.notes || '');
  };

  const handleCancelEdit = () => {
    setEditingIp(null);
    setIp('');
    setAlias('');
    setNotes('');
  };

  const handleDelete = async (targetIp: string) => {
    if (!window.confirm(`Are you sure you want to remove the alias for IP "${targetIp}"?`)) {
      return;
    }
    try {
      await deleteAlias(targetIp);
      await loadAliases();
      if (onAliasSaved) onAliasSaved();
    } catch (err: any) {
      setErrorMsg(err.message || 'Failed to delete host alias.');
    }
  };

  return (
    <div className="max-w-5xl mx-auto p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-base font-bold text-slate-100 flex items-center gap-2">
            <Server className="w-5 h-5 text-accent-500" />
            <span>Host Alias Manager</span>
          </h2>
          <p className="text-xs text-slate-400 mt-0.5">
            Map incoming source IP addresses to friendly host names (e.g. 192.168.1.1 → OPNsense Firewall).
          </p>
        </div>
      </div>

      {errorMsg && (
        <div className="p-3 bg-red-950/60 border border-red-800 rounded-lg flex items-start gap-2 text-xs text-red-300">
          <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
          <span>{errorMsg}</span>
        </div>
      )}

      {/* Add / Edit Form Card */}
      <div className="bg-dark-900 border border-dark-700 rounded-xl p-4 shadow-md">
        <h3 className="text-xs font-semibold text-slate-300 uppercase tracking-wider mb-3">
          {editingIp ? `Edit Mapping for ${editingIp}` : 'Add New Host Mapping'}
        </h3>
        <form onSubmit={handleSave} className="grid grid-cols-1 sm:grid-cols-3 gap-3 items-end">
          <div>
            <label className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
              Source IP Address
            </label>
            <input
              type="text"
              value={ip}
              onChange={(e) => setIp(e.target.value)}
              disabled={Boolean(editingIp)}
              placeholder="e.g. 192.168.1.50"
              required
              className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono disabled:opacity-50"
            />
          </div>

          <div>
            <label className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
              Friendly Host Alias
            </label>
            <input
              type="text"
              value={alias}
              onChange={(e) => setAlias(e.target.value)}
              placeholder="e.g. Proxmox-Node-01"
              required
              className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500 font-mono"
            />
          </div>

          <div>
            <label className="block text-[11px] font-semibold text-slate-400 uppercase mb-1">
              Notes (Optional)
            </label>
            <input
              type="text"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              placeholder="e.g. Main hypervisor in rack 1"
              className="w-full bg-dark-950 border border-dark-700 rounded px-3 py-2 text-xs text-slate-100 placeholder-slate-500 focus:outline-hidden focus:border-accent-500"
            />
          </div>

          <div className="sm:col-span-3 flex justify-end gap-2 pt-1">
            {editingIp && (
              <button
                type="button"
                onClick={handleCancelEdit}
                className="px-3 py-1.5 bg-dark-800 hover:bg-dark-700 text-slate-300 rounded text-xs transition"
              >
                Cancel
              </button>
            )}
            <button
              type="submit"
              disabled={isSaving || !ip.trim() || !alias.trim()}
              className="bg-accent-600 hover:bg-accent-500 disabled:opacity-50 text-white font-medium px-4 py-1.5 rounded text-xs flex items-center gap-1.5 transition shadow-xs"
            >
              {editingIp ? <Check className="w-3.5 h-3.5" /> : <Plus className="w-3.5 h-3.5" />}
              <span>{isSaving ? 'Saving...' : editingIp ? 'Update Mapping' : 'Add Mapping'}</span>
            </button>
          </div>
        </form>
      </div>

      {/* Aliases Table Card */}
      <div className="bg-dark-900 border border-dark-700 rounded-xl overflow-hidden shadow-md">
        <div className="p-3 bg-dark-950 border-b border-dark-700 flex items-center justify-between">
          <span className="text-xs font-semibold text-slate-300 uppercase tracking-wider">
            Active Host Mappings ({aliases.length})
          </span>
        </div>

        {isLoading ? (
          <div className="p-6 text-center text-slate-500 font-mono text-xs">Loading mappings...</div>
        ) : aliases.length === 0 ? (
          <div className="p-6 text-center text-slate-500 font-mono text-xs">
            No host aliases mapped yet. Add a mapping above to label incoming syslog IP addresses.
          </div>
        ) : (
          <div className="divide-y divide-dark-800 font-mono text-xs">
            <div className="grid grid-cols-[160px_200px_1fr_100px] px-4 py-2 text-slate-400 font-semibold text-[11px] bg-dark-950/60 select-none">
              <div>SOURCE IP</div>
              <div>FRIENDLY ALIAS</div>
              <div>NOTES</div>
              <div className="text-right">ACTIONS</div>
            </div>

            {aliases.map((item) => (
              <div
                key={item.ip}
                className="grid grid-cols-[160px_200px_1fr_100px] px-4 py-2.5 items-center hover:bg-dark-800/50 transition"
              >
                <div className="text-accent-400 font-semibold">{item.ip}</div>
                <div className="text-slate-200">{item.alias}</div>
                <div className="text-slate-400 font-sans text-xs truncate pr-2">
                  {item.notes || '—'}
                </div>
                <div className="flex items-center justify-end gap-1 font-sans">
                  <button
                    onClick={() => handleEdit(item)}
                    title="Edit Alias"
                    className="p-1 text-slate-400 hover:text-slate-200 hover:bg-dark-700 rounded transition"
                  >
                    <Edit2 className="w-3.5 h-3.5" />
                  </button>
                  <button
                    onClick={() => handleDelete(item.ip)}
                    title="Delete Alias"
                    className="p-1 text-slate-400 hover:text-red-400 hover:bg-dark-700 rounded transition"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
};
