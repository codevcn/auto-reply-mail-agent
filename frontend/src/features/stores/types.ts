export interface StoreFormData {
  // Step 1: Store identity & brand settings
  name: string;
  brand_name: string;
  public_domain: string;
  canonical_domain: string;
  mailbox_address: string;
  mailbox_password?: string;
  brand_voice: string;
  email_signature: string;
  brand_description: string;
  default_language: string;

  // Step 2: Proxy configuration
  proxy_host: string;
  proxy_port: string;
  proxy_username: string;
  proxy_password?: string;
  proxy_protocol: string;

  // Step 3: Shopify Dev App credentials
  shopify_client_id: string;
  shopify_client_secret: string;
}

export interface ConnectionTestStatus {
  tested: boolean;
  loading: boolean;
  success: boolean;
  message?: string;
  error?: string;
}

export interface ProxyTestStatus extends ConnectionTestStatus {
  exitIp?: string;
  country?: string;
  latencyMs?: number;
}

export interface ShopifyTestStatus extends ConnectionTestStatus {
  shopName?: string;
  scopes?: string[];
}
