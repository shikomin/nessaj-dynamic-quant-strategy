package com.nessaj.quant.scada.auth.entity;

import lombok.Data;

import java.time.LocalDateTime;

@Data
public class UserWatchlist {
    private Long id;
    private Long userId;
    private String stockCode;
    private Integer sortOrder;
    private LocalDateTime createdAt;
}
