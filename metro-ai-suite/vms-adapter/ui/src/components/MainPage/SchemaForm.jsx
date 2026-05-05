/**
 * SchemaForm — render a Pydantic-derived JSON Schema as form inputs.
 *
 * Supports the field shapes produced by FastAPI / Pydantic v2:
 *   - type: "string"  (with optional `enum`)
 *   - type: "integer" | "number"  (with optional `minimum` / `maximum`)
 *   - type: "boolean"
 *   - type: "array" of strings
 *   - JSON Schema "anyOf": [..., {type:"null"}]  → the non-null branch is used.
 *
 * Custom extensions honoured:
 *   - "x-vms-source": "camera"          → renders the camera dropdown using
 *                                         the `cameras` prop (enabled only).
 *   - "x-vms-source": "lvc-models" |
 *                     "lvc-pipelines"   → caller-supplied option list.
 */

import { useMemo } from 'react';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { Checkbox } from '@/components/ui/checkbox';
import {
  Select, SelectContent, SelectItem, SelectTrigger, SelectValue,
} from '@/components/ui/select';

function unwrapAnyOf(field) {
  if (field?.anyOf) {
    const nonNull = field.anyOf.find((b) => b.type !== 'null');
    return nonNull ? { ...nonNull, ...field, anyOf: undefined, type: nonNull.type } : field;
  }
  return field;
}

function fieldType(field) {
  const f = unwrapAnyOf(field);
  if (f.enum) return 'enum';
  if (f.type === 'array') return 'array';
  if (f.type === 'boolean') return 'boolean';
  if (f.type === 'integer' || f.type === 'number') return 'number';
  return 'string';
}

function defaultValue(field) {
  const f = unwrapAnyOf(field);
  if (f.default !== undefined) return f.default;
  switch (fieldType(field)) {
    case 'boolean': return false;
    case 'number':  return '';
    case 'array':   return [];
    default:        return '';
  }
}

// eslint-disable-next-line react-refresh/only-export-components
export function initialFormState(schema) {
  const props = schema?.properties ?? {};
  const out = {};
  for (const [key, field] of Object.entries(props)) {
    out[key] = defaultValue(field);
  }
  return out;
}

function FieldLabel({ children, required }) {
  return (
    <span className="text-[0.76rem] text-[#6B7BA4] font-medium">
      {children}
      {required && <span className="text-red-500 ml-0.5">*</span>}
    </span>
  );
}

function fieldError(errors, key) {
  if (!Array.isArray(errors)) return null;
  const hit = errors.find((e) => Array.isArray(e.loc) && e.loc.includes(key));
  return hit ? hit.msg : null;
}

function FieldRow({ name, field, value, onChange, options, error }) {
  const f = unwrapAnyOf(field);
  const ftype = fieldType(field);
  const title = f.title || name;
  const description = f.description;
  const required = options.requiredSet.has(name);

  let control;

  switch (ftype) {
    case 'boolean':
      control = (
        <label className="flex items-center gap-2 cursor-pointer h-8">
          <Checkbox
            checked={!!value}
            onCheckedChange={(v) => onChange(!!v)}
            className="data-[state=checked]:bg-[#0071C5] data-[state=checked]:border-[#0071C5]"
          />
          <span className="text-[0.82rem] text-[#374163]">{description || title}</span>
        </label>
      );
      break;

    case 'enum':
      control = (
        <Select value={value === '' || value == null ? '' : String(value)} onValueChange={onChange}>
          <SelectTrigger className="h-8 text-[0.82rem] border-[#DDE3F0] bg-[#F7F9FF]">
            <SelectValue placeholder="Select…" />
          </SelectTrigger>
          <SelectContent>
            {(f.enum ?? []).map((opt) => (
              <SelectItem key={String(opt)} value={String(opt)}>{String(opt)}</SelectItem>
            ))}
          </SelectContent>
        </Select>
      );
      break;

    case 'number':
      control = (
        <Input
          type="number"
          value={value ?? ''}
          min={f.minimum}
          max={f.maximum}
          step={f.type === 'integer' ? 1 : 'any'}
          placeholder={f.examples?.[0] ?? ''}
          onChange={(e) => {
            const v = e.target.value;
            onChange(v === '' ? '' : Number(v));
          }}
          className="h-8 text-[0.82rem] border-[#DDE3F0] bg-[#F7F9FF] font-mono-vms"
        />
      );
      break;

    case 'array':
      control = (
        <Input
          value={Array.isArray(value) ? value.join(', ') : (value ?? '')}
          placeholder="comma-separated"
          onChange={(e) => onChange(
            e.target.value.split(',').map((s) => s.trim()).filter(Boolean),
          )}
          className="h-8 text-[0.82rem] border-[#DDE3F0] bg-[#F7F9FF]"
        />
      );
      break;

    default: {
      // Honour x-vms-source hints for camera-bound fields and dynamic enums.
      const src = f['x-vms-source'];
      if (src === 'camera') {
        const cams = options.cameras ?? [];
        control = (
          <Select value={value ?? ''} onValueChange={onChange}>
            <SelectTrigger className="h-8 text-[0.82rem] border-[#DDE3F0] bg-[#F7F9FF]">
              <SelectValue placeholder={cams.length === 0 ? 'No enabled cameras' : 'Select camera…'} />
            </SelectTrigger>
            <SelectContent>
              {cams.map((c) => (
                <SelectItem key={c.camera_id} value={c.camera_id}>
                  {c.camera_name || c.name || c.camera_id}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        );
      } else if (src && options.dynamicOptions?.[src]) {
        const opts = options.dynamicOptions[src];
        control = (
          <Select value={value ?? ''} onValueChange={onChange}>
            <SelectTrigger className="h-8 text-[0.82rem] border-[#DDE3F0] bg-[#F7F9FF]">
              <SelectValue placeholder={`Select ${title.toLowerCase()}…`} />
            </SelectTrigger>
            <SelectContent>
              {opts.length === 0
                ? <SelectItem value="__none__" disabled>No options</SelectItem>
                : opts.map((o) => (
                    <SelectItem key={o.value} value={o.value}>{o.label}</SelectItem>
                  ))}
            </SelectContent>
          </Select>
        );
      } else {
        control = (
          <Input
            value={value ?? ''}
            placeholder={f.examples?.[0] ?? ''}
            onChange={(e) => onChange(e.target.value)}
            className="h-8 text-[0.82rem] border-[#DDE3F0] bg-[#F7F9FF]"
          />
        );
      }
    }
  }

  return (
    <div className="flex flex-col gap-[5px]">
      {ftype !== 'boolean' && <FieldLabel required={required}>{title}</FieldLabel>}
      {control}
      {description && ftype !== 'boolean' && (
        <span className="text-[0.7rem] text-[#8695B8]">{description}</span>
      )}
      {error && (
        <span className="text-[0.72rem] text-red-600 font-medium">{error}</span>
      )}
    </div>
  );
}

/**
 * Generic JSON Schema → form renderer.
 *
 * @param {Object}   schema          Pydantic JSON Schema (object with `properties`)
 * @param {Object}   value           Current form state
 * @param {Function} onChange        (next) => void  — receives the full next state
 * @param {Array}    [cameras]       Camera list for `x-vms-source: "camera"` fields
 * @param {Object}   [dynamicOptions] Map of `x-vms-source` key → [{value,label}]
 * @param {Array}    [errors]        Pydantic validation error array (loc/msg/type)
 */
export default function SchemaForm({
  schema,
  value,
  onChange,
  cameras = [],
  dynamicOptions = {},
  errors = [],
}) {
  const requiredSet = useMemo(
    () => new Set(Array.isArray(schema?.required) ? schema.required : []),
    [schema],
  );

  if (!schema || !schema.properties) {
    return <p className="text-[0.78rem] text-[#8695B8]">No parameters required.</p>;
  }

  const setField = (key) => (v) => onChange({ ...value, [key]: v });

  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 gap-[12px]">
      {Object.entries(schema.properties).map(([key, field]) => (
        <div
          key={key}
          className={fieldType(field) === 'boolean' ? 'sm:col-span-2' : ''}
        >
          <FieldRow
            name={key}
            field={field}
            value={value?.[key]}
            onChange={setField(key)}
            options={{ cameras, dynamicOptions, requiredSet }}
            error={fieldError(errors, key)}
          />
        </div>
      ))}
    </div>
  );
}
