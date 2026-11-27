"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, CircleDashed, Loader2, Pin, PinOff, RefreshCw } from "lucide-react";
import { useState } from "react";
import { Controller, useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { SettingGroup, SettingRow } from "@/components/settings/setting-row";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectSeparator,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { keys, useLlmSession, useProviders } from "@/hooks/queries";
import {
  deleteLlmSaved,
  deleteLlmSession,
  errorMessage,
  listModels,
  putLlmSaved,
  putLlmSession,
  testLlm,
  type LlmConfig,
  type ModelEntry,
  type ProviderId,
} from "@/lib/api";
import { loadPins, splitPinned, togglePin } from "@/lib/model-pins";
import { clearLlmConfig, saveLlmConfig } from "@/lib/session";

const MANUAL = "__manual__";
const PROVIDER_IDS = ["gemini", "anthropic", "openai", "openrouter", "openai_compatible"] as const;
const FIELD = "w-64";

const schema = z
  .object({
    provider: z.enum(PROVIDER_IDS),
    base_url: z.string().trim(),
    api_key: z.string().trim().min(1, "Paste your API key."),
    model_choice: z.string(),
    manual_model: z.string().trim(),
  })
  .superRefine((value, ctx) => {
    if (value.provider === "openai_compatible" && !/^https?:\/\/\S+$/.test(value.base_url)) {
      ctx.addIssue({
        code: "custom",
        path: ["base_url"],
        message: "Enter the base URL, e.g. http://127.0.0.1:1234/v1",
      });
    }
    const model =
      value.model_choice === MANUAL || !value.model_choice
        ? value.manual_model
        : value.model_choice;
    if (!model)
      ctx.addIssue({
        code: "custom",
        path: ["manual_model"],
        message: "Choose or type a model id.",
      });
  });
type FormValues = z.infer<typeof schema>;

function toConfig(values: FormValues): LlmConfig {
  const model =
    values.model_choice && values.model_choice !== MANUAL
      ? values.model_choice
      : values.manual_model;
  return {
    provider: values.provider,
    model,
    api_key: values.api_key,
    base_url: values.provider === "openai_compatible" ? values.base_url : null,
  };
}

/** Pin toggle inside a model row; it never selects the row (D-053). */
function PinButton({
  pinned,
  modelId,
  onToggle,
}: {
  pinned: boolean;
  modelId: string;
  onToggle: () => void;
}) {
  return (
    <button
      type="button"
      aria-label={pinned ? `Unpin ${modelId}` : `Pin ${modelId}`}
      title={pinned ? "Unpin" : "Pin to the top"}
      className={
        "hover:bg-muted hover:text-foreground ml-auto flex size-6 shrink-0 items-center justify-center rounded " +
        (pinned ? "text-foreground" : "text-muted-foreground")
      }
      // The row selects on pointer up (mouse) or click (touch): stop both at the button.
      onPointerDown={(event) => event.stopPropagation()}
      onPointerUp={(event) => event.stopPropagation()}
      onClick={(event) => {
        event.preventDefault();
        event.stopPropagation();
        onToggle();
      }}
    >
      {pinned ? (
        <PinOff className="size-3.5" aria-hidden />
      ) : (
        <Pin className="size-3.5" aria-hidden />
      )}
    </button>
  );
}

function FieldError({ message }: { message?: string }) {
  if (!message) return null;
  return (
    <p className="text-destructive -mt-1 pb-2 text-right text-xs" role="alert">
      {message}
    </p>
  );
}

/** Provider, key and model for the user's own LLM (D-053, compact layout D-067). */
export function ApiPane() {
  const client = useQueryClient();
  const providers = useProviders();
  const session = useLlmSession();
  const [models, setModels] = useState<ModelEntry[] | null>(null);
  const [pins, setPins] = useState<string[]>([]);
  const form = useForm<FormValues>({
    resolver: zodResolver(schema),
    defaultValues: {
      provider: "gemini",
      base_url: "",
      api_key: "",
      model_choice: "",
      manual_model: "",
    },
  });
  const provider = useWatch({ control: form.control, name: "provider" });
  const modelChoice = useWatch({ control: form.control, name: "model_choice" });
  const apiKey = useWatch({ control: form.control, name: "api_key" });
  const needsBaseUrl = provider === "openai_compatible";

  const load = useMutation({
    mutationFn: () => {
      const { provider: id, api_key, base_url } = form.getValues();
      return listModels(id, api_key.trim(), id === "openai_compatible" ? base_url.trim() : null);
    },
    onSuccess: (result) => {
      setPins(loadPins(form.getValues("provider")));
      setModels(result.models);
      if (result.models.length === 0) form.setValue("model_choice", MANUAL);
    },
    onError: () => {
      setModels([]);
      form.setValue("model_choice", MANUAL);
    },
  });
  const test = useMutation({ mutationFn: (values: FormValues) => testLlm(toConfig(values)) });
  const save = useMutation({
    mutationFn: async (values: FormValues) => {
      const config = toConfig(values);
      await putLlmSession(config);
      return config;
    },
    onSuccess: (config) => {
      saveLlmConfig(config);
      void client.invalidateQueries({ queryKey: keys.llmSession });
      form.setValue("api_key", "");
      toast.success("Saved for this session");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  // "Save": provider, model and key stored encrypted in the backend's database (D-053).
  const store = useMutation({
    mutationFn: (values: FormValues) => putLlmSaved(toConfig(values)),
    onSuccess: () => {
      // The backend loads it again on every start; the tab copy is not needed.
      clearLlmConfig();
      void client.invalidateQueries({ queryKey: keys.llmSession });
      form.setValue("api_key", "");
      toast.success("Saved on this PC");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const clear = useMutation({
    mutationFn: deleteLlmSession,
    onSuccess: () => {
      clearLlmConfig();
      void client.invalidateQueries({ queryKey: keys.llmSession });
      toast.success("Session cleared");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });
  const forget = useMutation({
    mutationFn: deleteLlmSaved,
    onSuccess: () => {
      void client.invalidateQueries({ queryKey: keys.llmSession });
      toast.success("Saved key deleted");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const pinToggle = (modelId: string) => setPins(togglePin(provider, modelId));
  const { pinned, others } = splitPinned(models ?? [], pins);
  const choicePinned = pins.includes(modelChoice);
  const saving = save.isPending || store.isPending;
  const errors = form.formState.errors;
  const providerLabel = (id: ProviderId | null | undefined) =>
    providers.data?.find((item) => item.id === id)?.label ?? id ?? "";
  const status = session.data;
  const manual = modelChoice === MANUAL || (models !== null && models.length === 0);

  return (
    <div className="flex flex-col gap-6">
      <p className="text-muted-foreground text-sm">
        Your own AI model picks the moments and writes the titles.
      </p>

      <SettingGroup title="Status">
        <SettingRow
          label={
            <span className="flex items-center gap-2">
              {status?.configured ? (
                <CheckCircle2 className="size-4 text-emerald-500" aria-hidden />
              ) : (
                <CircleDashed className="text-muted-foreground size-4" aria-hidden />
              )}
              {session.isPending ? "Checking…" : status?.configured ? "Connected" : "Not set up"}
            </span>
          }
          hint={
            session.isError ? (
              <span className="text-destructive">{errorMessage(session.error)}</span>
            ) : status?.configured ? (
              <>
                {providerLabel(status.provider)} · <span className="font-mono">{status.model}</span>
                {status.saved && " · saved on this PC"}
              </>
            ) : undefined
          }
        >
          {status?.configured && (
            <Button
              variant="ghost"
              size="sm"
              onClick={() => clear.mutate()}
              disabled={clear.isPending}
            >
              Clear session
            </Button>
          )}
          {status?.saved && (
            <Button
              variant="ghost"
              size="sm"
              className="text-destructive hover:text-destructive"
              onClick={() => forget.mutate()}
              disabled={forget.isPending}
            >
              Delete saved key
            </Button>
          )}
        </SettingRow>
      </SettingGroup>

      <form
        className="flex flex-col gap-6"
        onSubmit={form.handleSubmit((values) => save.mutate(values))}
        noValidate
      >
        <SettingGroup title="Connection">
          <SettingRow label="Provider" htmlFor="provider">
            <Controller
              control={form.control}
              name="provider"
              render={({ field }) => (
                <Select
                  value={field.value}
                  onValueChange={(value) => {
                    field.onChange(value);
                    setModels(null);
                    setPins([]);
                    form.setValue("model_choice", "");
                  }}
                >
                  <SelectTrigger id="provider" className={FIELD}>
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    {(providers.data ?? []).map((item) => (
                      <SelectItem key={item.id} value={item.id}>
                        {item.label}
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              )}
            />
          </SettingRow>
          {providers.isError && <FieldError message={errorMessage(providers.error)} />}

          {needsBaseUrl && (
            <>
              <SettingRow label="Base URL" htmlFor="base_url">
                <Input
                  id="base_url"
                  className={`${FIELD} font-mono`}
                  placeholder="http://127.0.0.1:1234/v1"
                  {...form.register("base_url")}
                />
              </SettingRow>
              <FieldError message={errors.base_url?.message} />
            </>
          )}

          <SettingRow label="API key" htmlFor="api_key">
            <Input
              id="api_key"
              type="password"
              autoComplete="off"
              placeholder="Paste key"
              className={FIELD}
              {...form.register("api_key")}
            />
          </SettingRow>
          <FieldError message={errors.api_key?.message} />

          <SettingRow
            label="Model"
            htmlFor="model"
            hint={
              load.isError ? (
                <span className="text-destructive">Couldn&apos;t load models. Type one.</span>
              ) : models ? (
                `${models.length} models`
              ) : undefined
            }
          >
            {models && modelChoice && modelChoice !== MANUAL && (
              <Button
                type="button"
                variant="ghost"
                size="icon-sm"
                aria-label={choicePinned ? "Unpin this model" : "Pin this model"}
                title={choicePinned ? "Unpin" : "Pin to the top"}
                onClick={() => pinToggle(modelChoice)}
              >
                {choicePinned ? <PinOff aria-hidden /> : <Pin aria-hidden />}
              </Button>
            )}
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              aria-label="Load models"
              title={apiKey.trim() ? "Load models" : "Paste a key first"}
              disabled={load.isPending || !apiKey.trim()}
              onClick={() => load.mutate()}
            >
              <RefreshCw className={load.isPending ? "animate-spin" : undefined} aria-hidden />
            </Button>
            <Controller
              control={form.control}
              name="model_choice"
              render={({ field }) => (
                <Select value={field.value} onValueChange={field.onChange}>
                  <SelectTrigger id="model" className={`${FIELD} font-mono`}>
                    <SelectValue placeholder={models ? "Choose a model" : "Load models"} />
                  </SelectTrigger>
                  <SelectContent>
                    {pinned.length > 0 && (
                      <>
                        <SelectGroup>
                          <SelectLabel>Pinned</SelectLabel>
                          {pinned.map((model) => (
                            <SelectItem
                              key={model.id}
                              value={model.id}
                              className="font-mono"
                              trailing={
                                <PinButton
                                  pinned
                                  modelId={model.id}
                                  onToggle={() => pinToggle(model.id)}
                                />
                              }
                            >
                              {model.id}
                            </SelectItem>
                          ))}
                        </SelectGroup>
                        <SelectSeparator />
                      </>
                    )}
                    {others.map((model) => (
                      <SelectItem
                        key={model.id}
                        value={model.id}
                        className="font-mono"
                        trailing={
                          <PinButton
                            pinned={false}
                            modelId={model.id}
                            onToggle={() => pinToggle(model.id)}
                          />
                        }
                      >
                        {model.id}
                      </SelectItem>
                    ))}
                    <SelectItem value={MANUAL}>Type a model id</SelectItem>
                  </SelectContent>
                </Select>
              )}
            />
          </SettingRow>
          {manual && (
            <SettingRow label="Model id">
              <Input
                aria-label="Model id"
                className={`${FIELD} font-mono`}
                placeholder="model-id"
                {...form.register("manual_model")}
              />
            </SettingRow>
          )}
          <FieldError message={errors.manual_model?.message} />
        </SettingGroup>

        <div className="flex flex-wrap items-center justify-end gap-2">
          <span className="mr-auto text-sm" role="status">
            {test.isSuccess && (
              <span className="flex items-center gap-1.5">
                <CheckCircle2 className="size-4 text-emerald-500" aria-hidden />
                Works ({test.data.latency_ms} ms)
              </span>
            )}
            {test.isError && <span className="text-destructive">{errorMessage(test.error)}</span>}
          </span>
          <Button
            type="button"
            variant="ghost"
            disabled={test.isPending}
            onClick={form.handleSubmit((values) => test.mutate(values))}
          >
            {test.isPending && <Loader2 className="animate-spin" aria-hidden />}
            Test
          </Button>
          <Button
            type="submit"
            variant="outline"
            disabled={saving}
            title="Kept in memory until Kliprix stops"
          >
            {save.isPending && <Loader2 className="animate-spin" aria-hidden />}
            Save for this session
          </Button>
          <Button
            type="button"
            disabled={saving}
            title="Stored encrypted on this PC"
            onClick={form.handleSubmit((values) => store.mutate(values))}
          >
            {store.isPending && <Loader2 className="animate-spin" aria-hidden />}
            Save
          </Button>
        </div>
      </form>
    </div>
  );
}
