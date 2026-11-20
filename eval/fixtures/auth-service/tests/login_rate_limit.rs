use auth_service::auth;

#[test]
fn login_rate_limit_allows_a_single_caller() {
    auth::reset();
    assert!(auth::allow_login().is_ok());
}
