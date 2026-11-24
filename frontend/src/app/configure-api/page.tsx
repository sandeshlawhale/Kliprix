"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2, Pin, PinOff } from "lucide-react";
import { useState } from "react";
import { Controller, useForm, useWatch } from "react-hook-form";
import { toast } from "sonner";
import { z } from "zod";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
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

export default function ConfigureApiPage() {
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
  });
  // "Save": provider, model and key stored encrypted in the backend's database (D-053).
  const store = useMutation({
    mutationFn: (values: FormValues) => putLlmSaved(toConfig(values)),
    onSuccess: () => {
      // The backend loads it again on every start; the tab copy is not needed.
      clearLlmConfig();
      void client.invalidateQueries({ queryKey: keys.llmSession });
      form.setValue("api_key", "");
      toast.success("Saved. Kliprix uses it again every time it starts.");
    },
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

  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Configure API</h1>
        <p className="text-muted-foreground">
          Kliprix uses your own LLM to pick moments and write titles. &ldquo;Save for this
          session&rdquo; keeps the key only in this browser tab and the backend&apos;s memory.
          &ldquo;Save&rdquo; stores it encrypted on this PC, with the model, so it is ready every
          time Kliprix starts.
        </p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Current status</CardTitle>
          <CardDescription>
            {session.isPending && "Checking…"}
            {session.isError && errorMessage(session.error)}
            {status?.configured && (
              <span className="flex items-center gap-2">
                <CheckCircle2 className="size-4" aria-hidden /> Configured:{" "}
                {providerLabel(status.provider)} · <span className="font-mono">{status.model}</span>
              </span>
            )}
            {status && !status.configured && "Not configured"}
          </CardDescription>
        </CardHeader>
        {status && (status.configured || status.saved) && (
          <CardContent className="flex flex-col gap-3">
            {status.saved && (
              <p className="text-muted-foreground text-sm">
                Saved on this PC (encrypted): {providerLabel(status.saved.provider)} ·{" "}
                <span className="font-mono">{status.saved.model}</span>
              </p>
            )}
            <div className="flex flex-wrap gap-2">
              {status.configured && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => clear.mutate()}
                  disabled={clear.isPending}
                >
                  Clear session
                </Button>
              )}
              {status.saved && (
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => forget.mutate()}
                  disabled={forget.isPending}
                >
                  Delete saved key
                </Button>
              )}
            </div>
          </CardContent>
        )}
      </Card>

      <form
        className="flex flex-col gap-5"
        onSubmit={form.handleSubmit((values) => save.mutate(values))}
        noValidate
      >
        <div className="flex flex-col gap-2">
          <Label htmlFor="provider">Provider</Label>
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
                <SelectTrigger id="provider" className="w-full">
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
          {providers.isError && (
            <p className="text-destructive text-sm">{errorMessage(providers.error)}</p>
          )}
        </div>

        {needsBaseUrl && (
          <div className="flex flex-col gap-2">
            <Label htmlFor="base_url">Base URL</Label>
            <Input
              id="base_url"
              className="font-mono"
              placeholder="http://127.0.0.1:1234/v1"
              {...form.register("base_url")}
            />
            {errors.base_url && (
              <p className="text-destructive text-sm">{errors.base_url.message}</p>
            )}
          </div>
        )}

        <div className="flex flex-col gap-2">
          <Label htmlFor="api_key">API key</Label>
          <Input id="api_key" type="password" autoComplete="off" {...form.register("api_key")} />
          {errors.api_key && <p className="text-destructive text-sm">{errors.api_key.message}</p>}
        </div>

        <div className="flex flex-col gap-2">
          <div className="flex items-center gap-3">
            <Button
              type="button"
              variant="outline"
              disabled={load.isPending || !apiKey.trim()}
              onClick={() => load.mutate()}
            >
              {load.isPending && <Loader2 className="animate-spin" aria-hidden />}
              Load models
            </Button>
            {models && !load.isError && (
              <span className="text-muted-foreground text-sm">{models.length} models</span>
            )}
          </div>
          {load.isError && (
            <p className="text-destructive text-sm" role="alert">
              {errorMessage(load.error)} You can type a model id below.
            </p>
          )}
        </div>

        <div className="flex flex-col gap-2">
          <Label htmlFor="model">Model</Label>
          <div className="flex items-center gap-2">
            <Controller
              control={form.control}
              name="model_choice"
              render={({ field }) => (
                <Select value={field.value} onValueChange={field.onChange}>
                  <SelectTrigger id="model" className="min-w-0 flex-1 font-mono">
                    <SelectValue
                      placeholder={models ? "Choose a model" : "Load models first, or enter one"}
                    />
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
                    <SelectItem value={MANUAL}>Enter model id manually</SelectItem>
                  </SelectContent>
                </Select>
              )}
            />
            {models && modelChoice && modelChoice !== MANUAL && (
              <Button
                type="button"
                variant="outline"
                size="icon"
                aria-label={choicePinned ? "Unpin this model" : "Pin this model"}
                title={choicePinned ? "Unpin this model" : "Pin this model to the top"}
                onClick={() => pinToggle(modelChoice)}
              >
                {choicePinned ? <PinOff aria-hidden /> : <Pin aria-hidden />}
              </Button>
            )}
          </div>
          {(modelChoice === MANUAL || (models !== null && models.length === 0)) && (
            <Input
              aria-label="Model id"
              className="font-mono"
              placeholder="model-id"
              {...form.register("manual_model")}
            />
          )}
          {errors.manual_model && (
            <p className="text-destructive text-sm">{errors.manual_model.message}</p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <Button
            type="button"
            variant="outline"
            disabled={test.isPending}
            onClick={form.handleSubmit((values) => test.mutate(values))}
          >
            {test.isPending && <Loader2 className="animate-spin" aria-hidden />}
            Test connection
          </Button>
          <Button type="submit" variant="outline" disabled={saving}>
            {save.isPending && <Loader2 className="animate-spin" aria-hidden />}
            Save for this session
          </Button>
          <Button
            type="button"
            disabled={saving}
            onClick={form.handleSubmit((values) => store.mutate(values))}
          >
            {store.isPending && <Loader2 className="animate-spin" aria-hidden />}
            Save
          </Button>
        </div>
        <p className="text-muted-foreground text-xs">
          Save keeps the key encrypted in Kliprix&apos;s database on this PC, together with the
          selected model. You can delete it any time under Current status.
        </p>
        {test.isSuccess && (
          <p className="text-sm" role="status">
            Connection works ({test.data.latency_ms} ms).
          </p>
        )}
        {test.isError && (
          <p className="text-destructive text-sm" role="alert">
            {errorMessage(test.error)}
          </p>
        )}
        {save.isError && (
          <p className="text-destructive text-sm" role="alert">
            {errorMessage(save.error)}
          </p>
        )}
        {store.isError && (
          <p className="text-destructive text-sm" role="alert">
            {errorMessage(store.error)}
          </p>
        )}
      </form>
    </div>
  );
}
