import { flushPromises, mount, type VueWrapper } from "@vue/test-utils";
import { createPinia, setActivePinia } from "pinia";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import * as accounts from "@/api/accounts";
import type { Account } from "@/api/accounts";
import EditDialog from "@/components/accounts/EditDialog.vue";
import ImportDialog from "@/components/accounts/ImportDialog.vue";
import { notify } from "@/lib/notify";
import { useScopeStore } from "@/stores/scope";

vi.mock("@/api/accounts", () => ({ importAccounts: vi.fn() }));
vi.mock("@/lib/notify", () => ({ notify: { success: vi.fn(), error: vi.fn(), info: vi.fn() } }));

const account = (over: Partial<Account> = {}): Account => ({
  id: "a1",
  provider: "vibes",
  label: "main",
  identity: null,
  display_name: null,
  state: "available",
  state_reason: null,
  proxy_host: "10.0.0.1",
  proxy_port: 8080,
  has_proxy_auth: true,
  proxy_ok: null,
  proxy_checked_ms: null,
  strikes: 0,
  cooldown_ms: null,
  cooldown_reason: null,
  last_error: null,
  last_used_ms: null,
  last_checked_ms: null,
  in_use: false,
  has_screenshot: false,
  composer: null,
  capacity: [],
  made: { video: 0, image: 0 },
  ...over,
});

let wrapper: VueWrapper | null = null;
const field = (label: string) => document.body.querySelector<HTMLInputElement>(`[aria-label="${label}"]`)!;
const buttonOf = (text: string) =>
  [...document.body.querySelectorAll("button")].find((b) => b.textContent?.trim() === text)!;

function type(el: HTMLInputElement | HTMLTextAreaElement, value: string) {
  el.value = value;
  el.dispatchEvent(new Event("input"));
}

beforeEach(() => {
  setActivePinia(createPinia());
  vi.mocked(accounts.importAccounts).mockReset();
  for (const fn of Object.values(notify)) (fn as ReturnType<typeof vi.fn>).mockReset();
});
afterEach(() => {
  wrapper?.unmount();
  wrapper = null;
  document.body.replaceChildren();
});

describe("EditDialog", () => {
  const mountEdit = async (a: Account) => {
    wrapper = mount(EditDialog, {
      props: { modelValue: true, account: a, busy: false },
      attachTo: document.body,
    });
    await flushPromises();
    return wrapper;
  };

  it("starts from the account: its label and its proxy as host:port", async () => {
    await mountEdit(account());
    expect(field("Label").value).toBe("main");
    expect(field("Proxy").value).toBe("10.0.0.1:8080");
  });

  it("Save sends the changed fields only", async () => {
    const w = await mountEdit(account());
    type(field("Label"), "renamed");
    buttonOf("Save").click();
    await flushPromises();
    expect(w.emitted("save")).toEqual([[{ label: "renamed" }]]);
  });

  it("an emptied proxy is sent as null", async () => {
    const w = await mountEdit(account());
    type(field("Proxy"), "");
    buttonOf("Save").click();
    await flushPromises();
    expect(w.emitted("save")).toEqual([[{ proxy: null }]]);
  });

  it("nothing changed: Save only closes the dialog", async () => {
    const w = await mountEdit(account());
    buttonOf("Save").click();
    await flushPromises();
    expect(w.emitted("save")).toBeUndefined();
    expect(w.emitted("update:modelValue")).toEqual([[false]]);
  });

  it("Re-check proxy closes the dialog and asks the view to queue it", async () => {
    const a = account();
    const w = await mountEdit(a);
    buttonOf("Re-check proxy").click();
    await flushPromises();
    expect(w.emitted("recheck")).toEqual([[a]]);
    expect(w.emitted("update:modelValue")).toEqual([[false]]);
  });
});

describe("ImportDialog", () => {
  const mountImport = async () => {
    wrapper = mount(ImportDialog, { props: { modelValue: false }, attachTo: document.body });
    await wrapper.setProps({ modelValue: true });
    await flushPromises();
    return wrapper;
  };
  const seedCatalog = () => {
    useScopeStore().models = [
      { id: "m1", owned_by: "vibes" },
      { id: "m2", owned_by: "dola" },
    ];
  };

  it("an empty paste says so and sends nothing", async () => {
    seedCatalog();
    await mountImport();
    buttonOf("Import").click();
    await flushPromises();
    expect(notify.error).toHaveBeenCalledWith("Paste at least one credential");
    expect(accounts.importAccounts).not.toHaveBeenCalled();
  });

  it("the provider starts as the filter, and the lines go out with the shared proxy", async () => {
    seedCatalog();
    useScopeStore().provider = "vibes";
    vi.mocked(accounts.importAccounts).mockResolvedValue({
      imported: [{ index: 0, id: "x", label: "a" }],
      rejected: [{ index: 1, reason: "credential_unparseable" }],
    });
    const w = await mountImport();
    type(document.body.querySelector("textarea")!, " cred-one \n\ncred-two\n");
    type(field("Proxy for all"), "h:1:u:p");
    buttonOf("Import").click();
    await flushPromises();
    expect(accounts.importAccounts).toHaveBeenCalledWith({
      provider: "vibes",
      items: [
        { credential: "cred-one", proxy: "h:1:u:p" },
        { credential: "cred-two", proxy: "h:1:u:p" },
      ],
    });
    expect(document.body.querySelector(".import-result")!.textContent).toBe(
      "Imported 1, rejected 1 (line 2: credential_unparseable)",
    );
    expect(document.body.querySelector("textarea")!.value).toBe("");
    expect(w.emitted("imported")).toHaveLength(1);
  });

  it("with no filter the provider is the first one", async () => {
    seedCatalog();
    vi.mocked(accounts.importAccounts).mockResolvedValue({ imported: [], rejected: [] });
    await mountImport();
    type(document.body.querySelector("textarea")!, "c");
    buttonOf("Import").click();
    await flushPromises();
    expect(vi.mocked(accounts.importAccounts).mock.calls[0]![0].provider).toBe("dola");
  });
});
