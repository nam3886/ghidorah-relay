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
  cooldown_until_ms: null,
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

  it("is titled with the account label, and Cancel closes it without saving", async () => {
    const w = await mountEdit(account({ label: "acct-n11-05" }));
    expect(document.body.querySelector("h2")!.textContent).toBe("Edit account — acct-n11-05");
    buttonOf("Cancel").click();
    await flushPromises();
    expect(w.emitted("update:modelValue")).toEqual([[false]]);
    expect(w.emitted("save")).toBeUndefined();
  });

  it("shows the result of the last proxy check beside the field, with the stored reason of a failure", async () => {
    await mountEdit(account({ proxy_ok: false, last_error: "proxy check: connect timeout after 8 s" }));
    const status = document.body.querySelector("[role=status]")!;
    expect(status.textContent).toContain("failed · connect timeout after 8 s");
    expect(status.querySelector("[data-tone=bad]")).not.toBeNull();
  });

  it("a new copy of the same account keeps what was typed; another account fills the fields again", async () => {
    const a = account();
    const w = await mountEdit(a);
    type(field("Label"), "renamed");
    await w.setProps({ account: { ...a, label: "from the server" } });
    expect(field("Label").value).toBe("renamed");
    await w.setProps({ account: account({ id: "a2", label: "other" }) });
    expect(field("Label").value).toBe("other");
  });

  it("an account without a proxy has no proxy status line", async () => {
    await mountEdit(account({ proxy_host: null, proxy_port: null }));
    expect(document.body.querySelector("[role=status]")).toBeNull();
  });

  it("Check proxy asks the view to queue it and stays open; its chip takes the status line, locked while it runs", async () => {
    const a = account();
    const w = await mountEdit(a);
    buttonOf("Check proxy").click();
    await flushPromises();
    expect(w.emitted("recheck")).toEqual([[a]]);
    expect(w.emitted("update:modelValue")).toBeUndefined();
    await w.setProps({ proxyChip: { state: "running", settled: false, read: 1 } });
    expect(buttonOf("Check proxy").getAttribute("aria-disabled")).toBe("true");
    buttonOf("Check proxy").click();
    expect(w.emitted("recheck")).toHaveLength(1);
    expect(document.body.querySelector("[data-state=running]")!.textContent?.trim()).toBe("running…");
    await w.setProps({ proxyChip: { state: "error", message: "connect timeout after 8 s", settled: true, read: 1 } });
    expect(document.body.querySelector("[data-state=error]")!.textContent?.trim()).toBe(
      "error · connect timeout after 8 s",
    );
    expect(buttonOf("Check proxy").getAttribute("aria-disabled")).toBeNull();
    await w.setProps({ proxyChip: null });
    expect(document.body.querySelector("[data-state]")).toBeNull();
    expect(document.body.querySelector("[role=status]")!.textContent).toContain("not checked");
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
    const box = document.body.querySelector("[role=status][aria-label='Result of the previous import']")!;
    expect(box.textContent).toContain("Imported 1, rejected 1");
    expect(box.textContent).toContain("1 imported");
    expect(box.textContent).toContain("1 rejected");
    expect(box.querySelector("li")!.textContent).toContain("line 2");
    expect(box.querySelector("li")!.textContent).toContain("The line is not a cookie jar.");
    // a rejected line is named by its number and a sentence for its reason: the credential is never echoed back
    expect(box.textContent).not.toContain("cred-two");
    expect(document.body.querySelector("textarea")!.value).toBe("");
    expect(w.emitted("imported")).toHaveLength(1);
  });

  it("a clean import shows one pill and no list; the box is gone on the next opening", async () => {
    seedCatalog();
    vi.mocked(accounts.importAccounts).mockResolvedValue({
      imported: [{ index: 0, id: "x", label: "a" }],
      rejected: [],
    });
    const w = await mountImport();
    type(document.body.querySelector("textarea")!, "c");
    buttonOf("Import").click();
    await flushPromises();
    const box = document.body.querySelector("[role=status]")!;
    expect(box.textContent).toContain("Imported 1, rejected 0");
    expect(box.querySelectorAll(".pill")).toHaveLength(1);
    expect(box.querySelector("ul")).toBeNull();
    await w.setProps({ modelValue: false });
    await w.setProps({ modelValue: true });
    await flushPromises();
    expect(document.body.querySelector("[role=status]")).toBeNull();
  });

  it("Cancel closes the dialog", async () => {
    seedCatalog();
    const w = await mountImport();
    buttonOf("Cancel").click();
    await flushPromises();
    expect(w.emitted("update:modelValue")).toEqual([[false]]);
  });

  it("the Provider field is the drawn select: another provider picked from its list is the one sent", async () => {
    seedCatalog();
    vi.mocked(accounts.importAccounts).mockResolvedValue({ imported: [], rejected: [] });
    await mountImport();
    expect(document.body.querySelector("select")).toBeNull();
    const box = document.body.querySelector<HTMLButtonElement>("[role=combobox]")!;
    expect(document.getElementById(box.getAttribute("aria-labelledby")!)!.textContent).toBe("Provider");
    // drawn as the canvas draws it: the 14 px chevron, not the select's own arrow
    expect([...box.querySelectorAll("svg path")].map((p) => p.getAttribute("d"))).toEqual(["m6 9 6 6 6-6"]);
    box.click();
    await flushPromises();
    const vibes = [...document.body.querySelectorAll<HTMLElement>("[role=option]")].find(
      (o) => o.textContent?.trim() === "vibes",
    )!;
    vibes.click();
    await flushPromises();
    type(document.body.querySelector("textarea")!, "c");
    buttonOf("Import").click();
    await flushPromises();
    expect(vi.mocked(accounts.importAccounts).mock.calls[0]![0].provider).toBe("vibes");
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

describe("what was pasted never outlives its use", () => {
  const seedCatalog = () => {
    useScopeStore().models = [{ id: "m1", owned_by: "dola" }];
  };
  const mountImport = async () => {
    wrapper = mount(ImportDialog, { props: { modelValue: false }, attachTo: document.body });
    await wrapper.setProps({ modelValue: true });
    await flushPromises();
    return wrapper;
  };
  const fill = () => {
    type(document.body.querySelector("textarea")!, "cred-one\ncred-two");
    type(field("Proxy for all"), "h:1:u:p");
  };
  const empty = () => {
    expect(document.body.querySelector("textarea")!.value).toBe("");
    expect(field("Proxy for all").value).toBe("");
  };

  it("after a success both fields are empty, and the request still carried both", async () => {
    seedCatalog();
    vi.mocked(accounts.importAccounts).mockResolvedValue({ imported: [], rejected: [] });
    await mountImport();
    fill();
    buttonOf("Import").click();
    await flushPromises();
    expect(vi.mocked(accounts.importAccounts).mock.calls[0]![0].items[0]).toEqual({
      credential: "cred-one",
      proxy: "h:1:u:p",
    });
    empty();
  });

  it("the fields are empty while the request is still running", async () => {
    seedCatalog();
    let release!: () => void;
    vi.mocked(accounts.importAccounts).mockReturnValue(
      new Promise((r) => (release = () => r({ imported: [], rejected: [] }))),
    );
    await mountImport();
    fill();
    buttonOf("Import").click();
    await flushPromises();
    empty();
    release();
    await flushPromises();
  });

  it("after a failure both fields are empty too", async () => {
    seedCatalog();
    vi.mocked(accounts.importAccounts).mockRejectedValue(new Error("down"));
    await mountImport();
    fill();
    buttonOf("Import").click();
    await flushPromises();
    expect(notify.error).toHaveBeenCalled();
    empty();
  });

  it("a cancelled paste is gone when the dialog is opened again", async () => {
    seedCatalog();
    const w = await mountImport();
    fill();
    buttonOf("Cancel").click();
    await flushPromises();
    await w.setProps({ modelValue: true });
    await flushPromises();
    empty();
  });

  it("closing from outside (modelValue false) wipes the fields while the dialog is gone", async () => {
    seedCatalog();
    const w = await mountImport();
    fill();
    await w.setProps({ modelValue: false });
    await flushPromises();
    await w.setProps({ modelValue: true });
    await flushPromises();
    empty();
  });

  it("an empty paste keeps what is there (a proxy typed first is not lost) and sends nothing", async () => {
    seedCatalog();
    await mountImport();
    type(field("Proxy for all"), "h:1:u:p");
    buttonOf("Import").click();
    await flushPromises();
    expect(accounts.importAccounts).not.toHaveBeenCalled();
    expect(field("Proxy for all").value).toBe("h:1:u:p");
  });
});

describe("the dialog frame", () => {
  const stub = (phone: boolean) =>
    vi.stubGlobal("matchMedia", (q: string) => ({
      matches: phone && /max-width:\s*767/.test(q),
      addEventListener: () => {},
      removeEventListener: () => {},
    }));
  afterEach(() => vi.unstubAllGlobals());

  it("from 768 px it is a box with one Close at the right of the title", async () => {
    stub(false);
    wrapper = mount(EditDialog, {
      props: { modelValue: true, account: account(), busy: false },
      attachTo: document.body,
    });
    await flushPromises();
    const closes = document.body.querySelectorAll("[aria-label=Close]");
    expect(closes).toHaveLength(1);
    expect(document.body.querySelector(".acct-dialog")!.classList.contains("acct-dialog--sheet")).toBe(false);
    expect(document.body.querySelector(".acct-dialog__head button:last-child")).toBe(closes[0]);
  });

  it("below 768 px it is a full-screen sheet whose single Close is a 44 px Back before the title", async () => {
    stub(true);
    wrapper = mount(EditDialog, {
      props: { modelValue: true, account: account(), busy: false },
      attachTo: document.body,
    });
    await flushPromises();
    const closes = document.body.querySelectorAll("[aria-label=Close]");
    expect(closes).toHaveLength(1);
    expect(document.body.querySelector(".acct-dialog")!.classList.contains("acct-dialog--sheet")).toBe(true);
    expect(document.body.querySelector(".acct-dialog__head button:first-child")).toBe(closes[0]);
    (closes[0] as HTMLElement).click();
    await flushPromises();
    expect(wrapper.emitted("update:modelValue")).toEqual([[false]]);
  });
});
