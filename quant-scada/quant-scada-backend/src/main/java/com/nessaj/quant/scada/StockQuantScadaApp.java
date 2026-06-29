package com.nessaj.quant.scada;

import org.mybatis.spring.annotation.MapperScan;
import org.springframework.boot.SpringApplication;
import org.springframework.boot.autoconfigure.SpringBootApplication;

@SpringBootApplication
@MapperScan("com.nessaj.quant.scada.auth.mapper")
public class StockQuantScadaApp {
    public static void main(String[] args) {
        SpringApplication.run(StockQuantScadaApp.class);
    }
}