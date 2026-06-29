-- ============================================================
-- MySQL 表结构 v1.0
-- 数据库: quant_scada
-- ============================================================

-- ============================================================
-- 1. 用户表
-- ============================================================
create table user
(
    id         bigint auto_increment primary key,
    created_at datetime default CURRENT_TIMESTAMP null,
    email      varchar(100)                       null,
    nickname   varchar(50)                        null,
    password   varchar(255)                       not null,
    phone      varchar(20)                        null,
    updated_at datetime(6)                        null,
    username   varchar(50)                        not null,
    constraint UK_sb8bbouer5wak8vyiiy4pf2bx
        unique (username)
);

-- ============================================================
-- 2. 股票基础信息表
-- ============================================================
create table stock_base_info
(
    id          bigint auto_increment primary key,
    code        varchar(16)                        not null comment '股票代码 (如 000001)',
    create_time datetime default CURRENT_TIMESTAMP null,
    market      varchar(8)                         not null comment '市场 (A/HK)',
    name        varchar(64)                        not null comment '股票名称',
    sub_market  varchar(8)                         null comment '子市场 (SZ/SH/BJ)',
    sector      varchar(32)                        null comment '所属板块 (创业板/主板/科创板)',
    constraint UKi4a6aipeb0ky8a0nx16dqool2
        unique (market, code)
);

-- ============================================================
-- 3. 用户自选股表
--     每个用户可以收藏多只股票, 后续扩展: 分组/排序/备注
-- ============================================================
create table user_watchlist
(
    id         bigint auto_increment primary key,
    user_id    bigint                             not null comment '用户ID',
    stock_code varchar(16)                        not null comment '股票代码 (关联 stock_base_info.code)',
    sort_order int default 0                              comment '排序序号 (升序)',
    created_at datetime default CURRENT_TIMESTAMP null     comment '添加时间',
    constraint UK_user_watchlist unique (user_id, stock_code),
    constraint FK_watchlist_user foreign key (user_id) references user(id) on delete cascade
);
