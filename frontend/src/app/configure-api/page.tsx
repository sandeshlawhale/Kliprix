"use client";

import { zodResolver } from "@hookform/resolvers/zod";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { CheckCircle2, Loader2 } from "lucide-react";
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
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/ui/select";
import { keys, useLlmSession, useProviders } from "@/hooks/queries";
import {
  deleteLlmSession,
  errorMessage,
  listModels,
  putLlmSession,
  testLlm,
  type LlmConfig,
  type ModelEntry,
  type ProviderId,
} from "@/lib/api";
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

export default function ConfigureApiPage() {
  const client = useQueryClient();
  const providers = useProviders();
  const session = useLlmSession();
  const [models, setModels] = useState<ModelEntry[] | null>(null);
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
  const clear = useMutation({
    mutationFn: deleteLlmSession,
    onSuccess: () => {
      clearLlmConfig();
      void client.invalidateQueries({ queryKey: keys.llmSession });
      toast.success("API settings cleared");
    },
    onError: (error) => toast.error(errorMessage(error)),
  });

  const errors = form.formState.errors;
  const providerLabel = (id: ProviderId | null | undefined) =>
    providers.data?.find((item) => item.id === id)?.label ?? id ?? "";

  return (
    <div className="flex max-w-2xl flex-col gap-6">
      <header className="flex flex-col gap-1">
        <h1 className="text-2xl font-semibold tracking-tight">Configure API</h1>
        <p className="text-muted-foreground">
          Kliprix uses your own LLM to pick moments and write titles. The key stays in this browser
          tab and in the backend&apos;s memory; it is never saved to disk.
        </p>
      </header>

      <Card>
        <CardHeader>
          <CardTitle className="text-base">Current status</CardTitle>
          <CardDescription>
            {session.isPending && "Checking…"}
            {session.isError && errorMessage(session.error)}
            {session.data?.configured && (
              <span className="flex items-center gap-2">
                <CheckCircle2 className="size-4" aria-hidden /> Configured:{" "}
                {providerLabel(session.data.provider)} ·{" "}
                <span className="font-mono">{session.data.model}</span>
              </span>
            )}
            {session.data && !session.data.configured && "Not configured"}
          </CardDescription>
        </CardHeader>
        {session.data?.configured && (
          <CardContent>
            <Button
              variant="outline"
              size="sm"
              onClick={() => clear.mutate()}
              disabled={clear.isPending}
            >
              Clear
            </Button>
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
          <Controller
            control={form.control}
            name="model_choice"
            render={({ field }) => (
              <Select value={field.value} onValueChange={field.onChange}>
                <SelectTrigger id="model" className="w-full font-mono">
                  <SelectValue
                    placeholder={models ? "Choose a model" : "Load models first, or enter one"}
                  />
                </SelectTrigger>
                <SelectContent>
                  {(models ?? []).map((model) => (
                    <SelectItem key={model.id} value={model.id} className="font-mono">
                      {model.id}
                    </SelectItem>
                  ))}
                  <SelectItem value={MANUAL}>Enter model id manually</SelectItem>
                </SelectContent>
              </Select>
            )}
          />
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
          <Button type="submit" disabled={save.isPending}>
            {save.isPending && <Loader2 className="animate-spin" aria-hidden />}
            Save for this session
          </Button>
        </div>
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
      </form>
    </div>
  );
}
