import React, { useState } from 'react';
import { Lock, Eye, EyeOff, Check } from 'lucide-react';
import { getServerToken, setServerToken } from '../lib/serverAuth';

// The self-host server password (OPENSHORTS_API_TOKEN in the server's .env).
// Kept in this browser only and sent to the OpenShorts server with every request.
export default function ServerPasswordInput({ onSaved }) {
    const [value, setValue] = useState(getServerToken());
    const [isVisible, setIsVisible] = useState(false);
    const [isSaved, setIsSaved] = useState(!!getServerToken());
    const [error, setError] = useState('');

    const handleSave = () => {
        const token = value.trim();
        if (!token) return;
        // Request headers carry plain ASCII only; the server's password is ASCII too.
        if (/[^\x20-\x7e]/.test(token)) {
            setError('The password has a character a request header cannot carry. Copy it again from the .env file.');
            return;
        }
        setError('');
        setServerToken(token);
        setIsSaved(true);
        onSaved?.();
    };

    return (
        <div className="card p-4 sm:p-6 mb-8 animate-fade">
            <div className="flex items-center gap-3 mb-4">
                <div className="p-2 bg-paper3 rounded-input text-brass">
                    <Lock size={18} />
                </div>
                <h2 className="font-display lowercase text-lg text-ink">Server password</h2>
            </div>

            <div className="flex flex-col sm:flex-row gap-3">
                <div className="relative sm:flex-1">
                    <input
                        id="server-password"
                        type={isVisible ? 'text' : 'password'}
                        value={value}
                        onChange={(e) => { setValue(e.target.value); setIsSaved(false); }}
                        onKeyDown={(e) => { if (e.key === 'Enter') handleSave(); }}
                        placeholder="OPENSHORTS_API_TOKEN from the server's .env"
                        autoComplete="off"
                        className="input-field pr-12 font-mono"
                    />
                    <button
                        type="button"
                        onClick={() => setIsVisible(!isVisible)}
                        aria-label={isVisible ? 'Hide password' : 'Show password'}
                        className="absolute right-3 top-1/2 -translate-y-1/2 text-muted hover:text-ink transition-colors"
                    >
                        {isVisible ? <EyeOff size={18} /> : <Eye size={18} />}
                    </button>
                </div>
                <button
                    type="button"
                    onClick={handleSave}
                    disabled={!value.trim() || isSaved}
                    className={isSaved ? 'badge-ok px-4 cursor-default' : 'btn-primary'}
                >
                    {isSaved ? <><Check size={14} /> Saved</> : 'Save'}
                </button>
            </div>
            {error && <p className="mt-3 text-xs text-danger" role="alert">{error}</p>}
            <p className="mt-3 text-xs text-muted">
                Your OpenShorts server asks for this password. It is stored in this browser
                and sent only to your OpenShorts server.
            </p>
        </div>
    );
}
